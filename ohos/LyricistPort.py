"""Lyricist 1.8.0 的独立 OHOS producer；不部署、不读取凭据。"""
from __future__ import annotations

import argparse
import time
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
from urllib.request import ProxyHandler, build_opener
import xml.etree.ElementTree as ET
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOOL_REVISION = "d0cff88fd1ea2e4f08bb4409f1c802cca0c7f285"
TOOL_DIR = "toolchains/kotlin-native/2.4.20-0.1.0-08"
GROUP = "io.github.qsxs.cpf.lyricist"
MODULES = ("lyricist", "lyricist-core")
ROOT = HERE.parent
def physical(path):
    path = Path(path)
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)
TASKS = {"Tasks": ["tasks", "--all"], "Resolve": ["resolveLyricist"], "Metadata": ["recordLyricistMetadata"],
         "NativeX64": ["recordLyricistX64"], "NativeArm64": ["recordLyricistArm64"], "Publish": ["publishLyricist"]}
COMPILES = (("Metadata", "metadata", None), ("NativeX64", "native-x64", "ohos_x64"),
            ("NativeArm64", "native-arm64", "ohos_arm64"))
REQUIRED = {"resolveLyricist", "recordLyricistMetadata", "recordLyricistX64", "recordLyricistArm64", "publishLyricist"}
REQUIRED |= {module + ":" + task for module in MODULES for task in
             ("compileCommonMainKotlinMetadata", "allMetadataJar", "compileKotlinOhosX64", "compileKotlinOhosArm64")}



def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(path):
    path = Path(path)
    data = physical(path).read_bytes()
    return {"path": str(path.resolve()), "size": len(data), "sha256": digest(data)}


def load(path):
    return json.loads(physical(path).read_text(encoding="utf-8"))


def write(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8") as output:
            output.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def safe_environment(work=None):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USERPROFILE", "SystemRoot", "ComSpec", "JAVA_HOME", "LANG", "LC_ALL", "SSL_CERT_FILE",
                                              "SSL_CERT_DIR", "LD_LIBRARY_PATH") if key in os.environ}
    if work is not None:
        for key, folder in (("GRADLE_USER_HOME", "gradle-home"), ("KONAN_DATA_DIR", "konan"),
                            ("TMPDIR", "temp"), ("TEMP", "temp"), ("TMP", "temp")):
            path = Path(work) / folder
            path.mkdir(parents=True, exist_ok=True)
            env[key] = str(path)
    return env


def checked_tools(work, explicit):
    lock = load(HERE / "tooling-lock.json")
    if lock["revision"] != TOOL_REVISION or lock["repository"] != "https://github.com/qsxs/cpf-port-tooling":
        raise RuntimeError("Immutable shared tooling revision differs")
    if lock["archiveUrl"] != lock["repository"] + "/archive/" + TOOL_REVISION + ".tar.gz":
        raise RuntimeError("Shared tooling archive URL differs")
    if len(lock["files"]) != 20 or len({row["path"] for row in lock["files"]}) != 20:
        raise RuntimeError("Shared tooling file lock differs")
    directory = Path(explicit).resolve() if explicit else (work / "tooling" / TOOL_REVISION).resolve()
    for row in lock["files"]:
        relative = PurePosixPath(row["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in row["path"] or not row["path"].startswith(TOOL_DIR + "/"):
            raise RuntimeError("Unsafe shared tooling file path")
    if not explicit and not all((directory / row["path"]).is_file() for row in lock["files"]):
        # 仅读取锁定文件；归档压缩容器本身不作为内容身份。
        with build_opener(ProxyHandler({})).open(lock["archiveUrl"], timeout=60) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeError("Shared tooling archive is too large")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            for row in lock["files"]:
                matches = [member for member in members if "/" in member.name and member.name.split("/", 1)[1] == row["path"]]
                if len(matches) != 1 or not matches[0].isfile():
                    raise RuntimeError("Shared tooling archive member differs: " + row["path"])
                stream = archive.extractfile(matches[0])
                if stream is None:
                    raise RuntimeError("Shared tooling archive member is unreadable")
                content = stream.read(row["size"] + 1)
                if len(content) != row["size"] or digest(content) != row["sha256"]:
                    raise RuntimeError("Shared tooling archive bytes differ: " + row["path"])
                target = directory / row["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if target.read_bytes() != content:
                        raise RuntimeError("Existing shared tooling bytes differ")
                else:
                    with target.open("xb") as out:
                        out.write(content)
    for row in lock["files"]:
        actual = record(directory / row["path"])
        if (actual["size"], actual["sha256"]) != (row["size"], row["sha256"]):
            raise RuntimeError("Shared tooling identity differs: " + row["path"])
    return directory


def git_bytes(source_sha, path):
    env = safe_environment()
    env["GIT_NO_LAZY_FETCH"] = "1"
    return subprocess.check_output(["git", "-C", str(ROOT), "show", source_sha + ":" + path], env=env)


def source_digest(rows):
    compact = [{key: row[key] for key in ("member", "size", "sha256")} for row in sorted(rows, key=lambda item: item["member"])]
    return digest(json.dumps(compact, sort_keys=True, separators=(",", ":")).encode())


def source_facts(source_sha):
    model = load(HERE / "source-inputs.json")
    if model["upstream"]["commit"] != "89b8b6821b288164f71e5196f33d8f706c758fae" or set(model["modules"]) != set(MODULES):
        raise RuntimeError("Lyricist upstream identity differs")
    if digest(git_bytes(source_sha, "LICENSE.md")) != "b55f5b44f477d275044af430d3d8d1c0eb644bf586459c89226b216e1c225929":
        raise RuntimeError("Fork license blob differs")
    wrapper = {"gradle/wrapper/gradle-wrapper.jar": "7d3a4ac4de1c32b59bc6a4eb8ecb8e612ccd0cf1ae1e99f66902da64df296172",
               "gradle/wrapper/gradle-wrapper.properties": "93b4f2c5ddcfc9e6f630fc7e004b31b389695cd0228be6da42e196e5d4af7acb"}
    for path, expected in wrapper.items():
        if record(HERE / path)["sha256"] != expected:
            raise RuntimeError("Approved Gradle wrapper differs: " + path)
    plugin = load(HERE / "compiler-plugin-toolchain.json")["sourcePom"]
    if (record(HERE / plugin["path"])["size"], record(HERE / plugin["path"])["sha256"]) != (plugin["size"], plugin["sha256"]):
        raise RuntimeError("Compose compiler plugin source POM differs")
    roots = ["lyricist-compose/src/commonMain/kotlin", "lyricist-core/src/commonMain/kotlin"]
    env = safe_environment()
    env["GIT_NO_LAZY_FETCH"] = "1"
    tree = subprocess.check_output(["git", "-c", "core.longpaths=true", "-C", str(ROOT), "ls-tree", "-r", "-z", source_sha, "--", *roots], env=env)
    found = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        info, name = entry.decode("utf-8").split("\t", 1)
        mode, kind, oid = info.split(" ")
        if name.endswith(".kt"):
            if mode != "100644" or kind != "blob" or name in found:
                raise RuntimeError("Unexpected Lyricist Git source entry: " + name)
            found[name] = oid
    expected_paths = {path for module in model["modules"].values() for path in module["gitSources"].values()}
    if set(found) != expected_paths or len(found) != 2:
        raise RuntimeError("Lyricist Git source inventory differs")
    rows = []
    for name, module in sorted(model["modules"].items()):
        for key in ("sources", "pom", "gmm", "upstreamBuild", "license", "nativeReferencePom", "nativeReferenceGmm"):
            item = module[key]
            actual = record(HERE / item["path"])
            if (actual["size"], actual["sha256"]) != (item["size"], item["sha256"]):
                raise RuntimeError("Original upstream bytes differ: " + item["path"])
        expected = {item["member"]: item for item in module["sourceMembers"]}
        if len(expected) != 1 or set(expected) != set(module["gitSources"]):
            raise RuntimeError("Lyricist module source count differs: " + name)
        with zipfile.ZipFile(physical(HERE / module["sources"]["path"])) as archive:
            names = archive.namelist()
            if len(names) != module["archiveEntryCount"] or len(set(names)) != len(names) or {x for x in names if x.endswith(".kt")} != set(expected):
                raise RuntimeError("Official source archive inventory differs: " + name)
            for member, item in sorted(expected.items()):
                path = module["gitSources"][member]
                data = git_bytes(source_sha, path)
                if len(data) != item["size"] or digest(data) != item["sha256"] or archive.read(member) != data:
                    raise RuntimeError("Fork Git blob differs from official source bytes: " + member)
                rows.append({"module": name, "member": member, "size": len(data), "sha256": digest(data), "gitPath": path, "gitBlob": found[path]})
        if source_digest(module["sourceMembers"]) != module["sourceSetSha256"]:
            raise RuntimeError("Lyricist source set digest differs: " + name)
    return model, rows


def output_roots(args):
    root = HERE.resolve()
    work, repository = Path(args.work).resolve(), Path(args.repository).resolve()
    outputs = (work, repository)
    if work.is_relative_to(repository) or repository.is_relative_to(work):
        raise RuntimeError("Work and staging must be separate directories")
    if any(output.is_relative_to(ROOT.resolve()) or ROOT.resolve().is_relative_to(output) for output in outputs):
        raise RuntimeError("Work and staging must be outside the Fork checkout")
    if any(root.is_relative_to(output) for output in outputs):
        raise RuntimeError("Work and staging must not be or contain the recipe root")
    prepared = work / "prepared.json"
    if prepared.is_file():
        previous = load(prepared)
        if (previous["work"], previous["repository"]) != (str(work), str(repository)):
            raise RuntimeError("Prepared output paths differ")
        for item in previous["recipe"]:
            path = (root / item["path"]).resolve()
            if any(path.is_relative_to(output) for output in outputs):
                raise RuntimeError("Output path contains a recorded recipe file")
    return outputs


def recipe_inputs(outputs):
    root = HERE.resolve()
    recipe = []
    for current, directories, files in os.walk(root):
        parent = Path(current)
        directories[:] = [name for name in directories
                          if name not in (".gradle", ".kotlin", "build", "__pycache__")
                          and not any((parent / name).resolve().is_relative_to(output) for output in outputs)]
        for name in files:
            path = parent / name
            if path.is_file() and not any(path.resolve().is_relative_to(output) for output in outputs):
                data = path.read_bytes()
                recipe.append({"path": path.relative_to(root).as_posix(), "size": len(data), "sha256": digest(data)})
    return sorted(recipe, key=lambda item: item["path"])


def identity(args, tools, outputs):
    config = load(HERE / "port-config.json")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha) or not re.fullmatch(r"[1-9][0-9]*", args.run_id) or not re.fullmatch(r"[1-9][0-9]*", args.run_attempt):
        raise RuntimeError("Immutable source SHA and positive run identifiers are required")
    actual = subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(ROOT), "rev-parse", "HEAD"], env=safe_environment(), check=True, capture_output=True, text=True).stdout.strip()
    if actual != args.source_sha:
        raise RuntimeError("Lyricist checkout differs from requested source SHA")
    if config["group"] != GROUP or set(config["modules"]) != set(MODULES) or config["kotlin"] != "2.4.20-0.1.0-08" or config["metadataPolicy"] != "STANDARD_LOCKED_KGP_METADATA_NO_FALLBACK":
        raise RuntimeError("Lyricist port identity differs")
    model, rows = source_facts(args.source_sha)
    recipe = recipe_inputs(outputs)
    value = {"sourceCommit": args.source_sha, "sourceTagCommit": model["upstream"]["commit"], "sources": rows,
             "recipe": recipe, "toolingCommit": TOOL_REVISION, "toolingFiles": load(HERE / "tooling-lock.json")["files"],
             "group": GROUP, "modules": list(MODULES), "version": "1.8.0-cpf11-preview-" + args.source_sha[:12] + "-" + args.run_id + "-" + args.run_attempt}
    value["fingerprint"] = digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    value["toolingHome"] = str(tools)
    value["work"] = str(outputs[0])
    value["repository"] = str(outputs[1])
    return value


def verify_recipe_commit(source_sha, recipe):
    # 发布版本包含 source SHA，实际配方必须属于同一提交。
    env = safe_environment()
    env["GIT_NO_LAZY_FETCH"] = "1"
    tree = subprocess.check_output(["git", "-C", str(ROOT), "ls-tree", "-r", "-z", source_sha, "--", "ohos"], env=env)
    committed = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        info, path = entry.decode("utf-8").split("\t", 1)
        mode, kind, oid = info.split(" ")
        relative = path.removeprefix("ohos/")
        if any(part in (".gradle", ".kotlin", "build", "__pycache__") for part in PurePosixPath(relative).parts[:-1]):
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            raise RuntimeError("Unsupported committed recipe entry: " + path)
        committed[relative] = oid
    if set(committed) != {item["path"] for item in recipe}:
        raise RuntimeError("Recipe files differ from the requested committed source SHA")
    for item in recipe:
        data = (HERE / item["path"]).read_bytes()
        header = b"blob " + str(len(data)).encode("ascii") + b"\0"
        if hashlib.sha1(header + data).hexdigest() != committed[item["path"]]:
            raise RuntimeError("Recipe bytes differ from the requested committed source SHA: " + item["path"])


def check_native(home):
    if not home:
        raise RuntimeError("Explicit Kotlin/Native home is required")
    props = dict(line.split("=", 1) for line in (Path(home) / "konan/konan.properties").read_text(encoding="utf-8").splitlines() if "=" in line)
    if props.get("compilerVersion", "").strip() != "2.4.20-0.1.0-08":
        raise RuntimeError("Kotlin/Native compilerVersion differs")


def payload(outputs, target):
    has_metadata, has_ir, native_targets = False, False, []
    for item in outputs:
        if record(item["path"]) != item:
            raise RuntimeError("Compiler output bytes differ")
        path = physical(item["path"])
        props = None
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                has_metadata |= any(("/linkdata/" in n or n.endswith(".kotlin_metadata")) and archive.getinfo(n).file_size for n in names)
                has_ir |= any("/ir/" in n and archive.getinfo(n).file_size for n in names)
                if "default/manifest" in names:
                    props = archive.read("default/manifest").decode()
        elif path.as_posix().endswith("/default/manifest"):
            props = path.read_text(encoding="utf-8")
        has_metadata |= "/linkdata/" in path.as_posix() and item["size"] > 0
        has_ir |= "/ir/" in path.as_posix() and item["size"] > 0
        if props is not None:
            properties = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
            native_targets += properties.get("native_targets", "").split()
    if not has_metadata or (target and (not has_ir or set(native_targets) != {target})):
        raise RuntimeError("Compiler metadata, Native IR or target payload differs")


def verify_stage(value, evidence):
    work = Path(value["work"])
    models = load(HERE / "source-inputs.json")["modules"]
    config = load(HERE / "port-config.json")
    row = load(work / "evidence" / (evidence + ".json"))
    if (row["fingerprint"] != value["fingerprint"] or row["stage"] != evidence or
            row["sourceManifest"] != record(work / "source-manifest.json") or
            {item["module"] for item in row["modules"]} != set(models)):
        raise RuntimeError("Lyricist compilation evidence identity differs")
    compiler = {"metadata": "compileCommonMainKotlinMetadata", "native-x64": "compileKotlinOhosX64",
                "native-arm64": "compileKotlinOhosArm64"}[evidence]
    for item in row["modules"]:
        module = item["module"]
        model = models[module]
        expected = {}
        for source in model["sourceMembers"]:
            path = work / "sources" / module / Path(*PurePosixPath(source["member"]).parts)
            original = record(path)
            if (original["size"], original["sha256"]) != (source["size"], source["sha256"]):
                raise RuntimeError("Original compiler source bytes differ: " + source["member"])
            expected[path.resolve()] = (source["size"], source["sha256"])
        tasks = {":" + module + ":" + compiler}
        if evidence == "metadata":
            tasks.add(":" + module + ":allMetadataJar")
        actual = {Path(source["path"]).resolve(): (source["size"], source["sha256"]) for source in item["sources"]}
        explicit = config["modules"][module]["explicitApi"]
        if (actual != expected or item["sourceCount"] != len(expected) or
                item["sourceSetSha256"] != model["sourceSetSha256"] or item["publicApi"] != model["publicApi"] or
                {p["task"] for p in item["producers"]} != tasks or
                (item["explicitApi"] != "Strict" if explicit else item["explicitApi"] not in (None, "Disabled")) or
                item["sourceDependencies"] != model["sourceDependencies"]):
            raise RuntimeError("Complete compiler sources, API inventory, source scopes or standard producer differ: " + module)
        plugins = item["compilerPlugins"]
        plugin = load(HERE / "compiler-plugin-toolchain.json")["artifacts"]["org.jetbrains.kotlin:kotlin-compose-compiler-plugin-embeddable"]
        expected_plugins = {(plugin["size"], plugin["sha256"])} if module == "lyricist" else set()
        if (len(plugins) != len(expected_plugins) or {(p["size"], p["sha256"]) for p in plugins} != expected_plugins or
                any(record(p["path"]) != p for p in plugins)):
            raise RuntimeError("Compiler plugin identity differs: " + module)
        outputs = [output for producer in item["producers"] for output in producer["outputs"]]
        if not outputs or not any(output["size"] for output in outputs):
            raise RuntimeError("Lyricist producer outputs are absent: " + module)
        payload(outputs, None if evidence == "metadata" else "ohos_x64" if evidence == "native-x64" else "ohos_arm64")
    return row


def native_klib(outputs):
    manifests = [item for item in outputs if item["path"].replace("\\", "/").endswith("/default/manifest")]
    packed = [item for item in outputs if item["path"].endswith(".klib")]
    if len(manifests) == 1:
        return Path(manifests[0]["path"]).parent.parent
    if len(packed) == 1:
        return Path(packed[0]["path"])
    raise RuntimeError("Native Klib root is not unique")


def verify_signature_texts(model, metadata, ir, dump):
    declared = re.search(r"(?ms)^// Declared signatures: \d+\r?\n(.*?)^// Imported signatures: \d+\r?$", ir)
    if not metadata.strip() or not declared:
        raise RuntimeError("Metadata or Declared IR signatures are absent")
    declared_text = declared.group(1)
    matches, used = [], set()
    for item in model["publicApi"]["declarations"]:
        channel = item["evidenceChannel"]
        if channel == "metadata-dump":
            found = re.findall(item["metadataDumpPattern"], dump)
            if not found:
                raise RuntimeError("Public typealias missing from metadata dump: " + item["owner"])
            matches.append({"owner": item["owner"], "channel": channel, "matches": found})
            continue
        found = re.findall(item["metadataPattern"] + r"[^\r\n]*", metadata, re.M)
        # 同名重载按完整参数签名区分，同一签名不得抵扣另一条源码声明。
        found = [signature for signature in found if signature not in used]
        if len(found) != 1:
            raise RuntimeError("Public declaration metadata signature is not unique: " + item["owner"])
        used.add(found[0])
        matches.append({"owner": item["owner"], "sourceLine": item["sourceLine"], "channel": "metadata", "signature": found[0]})
    for item in model["publicApi"]["languageGeneratedMembers"]:
        found = re.findall(item["signaturePattern"] + r"[^\r\n]*", metadata, re.M)
        channel = "metadata"
        if not found:
            found = re.findall(item["signaturePattern"] + r"[^\r\n]*", declared_text, re.M)
            channel = "declaredIr"
        if len(found) != 1:
            raise RuntimeError("Language-generated member missing or ambiguous: " + item["owner"])
        matches.append({"owner": item["owner"], "channel": channel, "signature": found[0]})
    return matches


def signature_stage(value, evidence, attempt, native_home):
    stage = verify_stage(value, evidence)
    work = Path(value["work"])
    launcher = Path(native_home).resolve() / ("bin/klib.bat" if os.name == "nt" else "bin/klib")
    env = safe_environment(work)
    modules = []
    for module in stage["modules"]:
        outputs = [output for producer in module["producers"] for output in producer["outputs"]]
        dumps = {}
        for channel, command in (("metadata", "dump-metadata-signatures"), ("declaredIr", "dump-ir-signatures"), ("metadataDump", "dump-metadata")):
            argv = [str(launcher), command, str(native_klib(outputs))]
            receipt = work / "invocations" / f"{evidence}-{module['module']}-{channel}-{attempt}.json"
            receipt.parent.mkdir(parents=True, exist_ok=True)
            invocation = {"fingerprint": value["fingerprint"], "sourceSetSha256": module["sourceSetSha256"],
                          "module": module["module"], "argv": argv, "cwd": str(ROOT), "launcher": record(launcher),
                          "inputs": outputs, "started": time.time(), "exitCode": None}
            with physical(receipt).open("xb") as stream:
                stream.write((json.dumps(invocation, indent=2) + "\n").encode())
            with physical(receipt.with_suffix(".stdout.log")).open("xb") as out, physical(receipt.with_suffix(".stderr.log")).open("xb") as err:
                result = subprocess.run(argv, cwd=ROOT, env=env, stdout=out, stderr=err)
            output = record(receipt.with_suffix(".stdout.log"))
            invocation.update(exitCode=result.returncode, finished=time.time(), output=output)
            physical(receipt).write_bytes((json.dumps(invocation, indent=2) + "\n").encode())
            if result.returncode:
                raise RuntimeError("Native API dump failed; see " + str(receipt))
            dumps[channel] = {"invocation": record(receipt), "output": output}
        modules.append({"module": module["module"], "sourceSetSha256": module["sourceSetSha256"], "inputs": outputs, "dumps": dumps})
    write(work / "evidence" / (evidence + "-api.json"), {"fingerprint": value["fingerprint"], "stage": evidence,
          "modules": modules, "runtime": "NOT_RUN"}, exclusive=True)
    return verify_api(value, evidence, native_home)


def verify_api(value, evidence, native_home):
    stage = verify_stage(value, evidence)
    models = load(HERE / "source-inputs.json")["modules"]
    api = load(Path(value["work"]) / "evidence" / (evidence + "-api.json"))
    if api["fingerprint"] != value["fingerprint"] or api["stage"] != evidence or {m["module"] for m in api["modules"]} != set(models):
        raise RuntimeError("Native API evidence belongs to another producer")
    launcher = Path(native_home).resolve() / ("bin/klib.bat" if os.name == "nt" else "bin/klib")
    verified = []
    for module in api["modules"]:
        original = next(row for row in stage["modules"] if row["module"] == module["module"])
        outputs = [item for producer in original["producers"] for item in producer["outputs"]]
        if module["inputs"] != outputs or module["sourceSetSha256"] != original["sourceSetSha256"]:
            raise RuntimeError("Native API inputs differ from complete module")
        texts = {}
        for channel, command in (("metadata", "dump-metadata-signatures"), ("declaredIr", "dump-ir-signatures"), ("metadataDump", "dump-metadata")):
            dump = module["dumps"][channel]
            if record(dump["invocation"]["path"]) != dump["invocation"] or record(dump["output"]["path"]) != dump["output"]:
                raise RuntimeError("Native API invocation/output bytes differ")
            invocation = load(dump["invocation"]["path"])
            if (invocation["exitCode"] != 0 or invocation["inputs"] != outputs or invocation["module"] != module["module"] or
                    invocation["fingerprint"] != value["fingerprint"] or invocation["sourceSetSha256"] != original["sourceSetSha256"] or
                    invocation["output"] != dump["output"] or invocation["launcher"] != record(launcher) or
                    invocation["argv"] != [str(launcher), command, str(native_klib(outputs))] or
                    Path(dump["output"]["path"]).resolve() != Path(dump["invocation"]["path"]).with_suffix(".stdout.log").resolve()):
                raise RuntimeError("Native API command did not succeed for exact compiler output")
            texts[channel] = physical(dump["output"]["path"]).read_text(encoding="utf-8-sig")
        verified.append({"module": module["module"], "sourceSetSha256": module["sourceSetSha256"],
                         "matches": verify_signature_texts(models[module["module"]], texts["metadata"], texts["declaredIr"], texts["metadataDump"])})
    return {"receipt": api, "verified": verified}


def verify_publication(value, native_home):
    source_facts(value["sourceCommit"])
    config = load(HERE / "port-config.json")
    models = load(HERE / "source-inputs.json")["modules"]
    pins = load(HERE / "dependency-pins.json")
    stages = {stage: verify_stage(value, stage) for _, stage, _ in COMPILES}
    private_versions = {GROUP + ":" + m: value["version"] for m in MODULES}
    rows, scopes = [], []
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    for module, model in models.items():
        group, version = GROUP, value["version"]
        repository = physical(value["repository"]) / group.replace(".", "/")
        root_file = repository / module / version / f"{module}-{version}.module"
        root = load(root_file)
        expected_roots = {"org.jetbrains.kotlin:kotlin-stdlib", *model["sourceDependencies"]["api"], *model["sourceDependencies"]["implementation"]}
        for suffix, target in (("", None), ("-ohosx64", "ohos_x64"), ("-ohosarm64", "ohos_arm64")):
            name = module + suffix
            directory = repository / name / version
            gmm_file = directory / f"{name}-{version}.module"
            gmm = load(gmm_file)
            if any(gmm["component"].get(k) != v for k, v in {"group": group, "module": module, "version": version}.items()):
                raise RuntimeError("Lyricist publication owner differs: " + name)
            if target:
                if (directory / gmm["component"]["url"]).resolve() != root_file.resolve():
                    raise RuntimeError("Lyricist Native leaf owner URL differs")
                links = [v["available-at"] for v in root["variants"] if v.get("attributes", {}).get("org.jetbrains.kotlin.native.target") == target and "available-at" in v]
                if not links or any(link["group"] != group or link["module"] != name or link["version"] != version or
                                    (root_file.parent / link["url"]).resolve() != gmm_file.resolve() for link in links):
                    raise RuntimeError("Lyricist root/Native available-at differs")
            api = []
            for variant in gmm["variants"]:
                attrs = variant.get("attributes", {})
                if ((not target and attrs.get("org.gradle.usage") == "kotlin-metadata" and attrs.get("org.jetbrains.kotlin.platform.type") == "common") or
                        (target and attrs.get("org.gradle.usage") == "kotlin-api" and attrs.get("org.jetbrains.kotlin.native.target") == target)):
                    api.append(variant)
                for dep in variant.get("dependencies", []):
                    ga = dep["group"] + ":" + dep["module"]
                    if private_versions.get(ga, pins.get(ga)) != dep.get("version", {}).get("requires"):
                        raise RuntimeError("Lyricist GMM dependency escaped exact private/pinned versions")
                for artifact in variant.get("files", []):
                    path = (directory / artifact["url"]).resolve()
                    if not path.is_relative_to(directory.resolve()):
                        raise RuntimeError("Lyricist artifact URL escaped its publication")
                    actual = record(path)
                    if actual["sha256"] != artifact["sha256"] or actual["size"] != artifact["size"] or not actual["size"]:
                        raise RuntimeError("Lyricist published artifact bytes differ")
            if (not api or not all(v.get("files") for v in api) or
                    any({d["group"] + ":" + d["module"] for d in v.get("dependencies", [])} != expected_roots for v in api)):
                raise RuntimeError("Lyricist common/Native GMM API dependency set differs from original scopes")
            pom = ET.fromstring((directory / f"{name}-{version}.pom").read_bytes())
            if any(pom.findtext("m:" + k, namespaces=ns) != v for k, v in (("groupId", group), ("artifactId", name), ("version", version))):
                raise RuntimeError("Lyricist POM coordinates differ")
            if (pom.findtext("m:licenses/m:license/m:name", namespaces=ns) != "MIT License" or
                    pom.findtext("m:licenses/m:license/m:url", namespaces=ns) != "https://opensource.org/licenses/MIT" or
                    pom.findtext("m:scm/m:url", namespaces=ns) != "https://github.com/adrielcafe/lyricist"):
                raise RuntimeError("Upstream license or SCM identity differs")
            developers = [(d.findtext("m:id", namespaces=ns), d.findtext("m:name", namespaces=ns), d.findtext("m:url", namespaces=ns)) for d in pom.findall("m:developers/m:developer", ns)]
            if developers != [("adrielcafe", "Adriel Cafe", "https://github.com/adrielcafe/")]:
                raise RuntimeError("Upstream POM developer identity differs")
            if record(directory / f"{name}-{version}-provenance.json")["sha256"] != record(Path(value["work"]) / "provenance.json")["sha256"]:
                raise RuntimeError("Published provenance differs")
            expected_pom = {ga + (suffix if target and not ga.startswith("org.jetbrains.kotlin:") else "") for ga in expected_roots}
            actual_pom = set()
            for dep in pom.findall("m:dependencies/m:dependency", ns):
                ga = dep.findtext("m:groupId", namespaces=ns) + ":" + dep.findtext("m:artifactId", namespaces=ns)
                actual_pom.add(ga)
                root_ga = ga.removesuffix(suffix) if suffix else ga
                expected_version = private_versions.get(root_ga, pins.get(ga))
                if expected_version != dep.findtext("m:version", namespaces=ns) or dep.findtext("m:scope", namespaces=ns) != ("compile" if target else "runtime"):
                    raise RuntimeError("Lyricist POM version or publication-specific scope differs")
            if actual_pom != expected_pom:
                raise RuntimeError("Lyricist POM dependency closure differs")
            scopes.append({"module": module, "publication": name, "sourceDependencies": model["sourceDependencies"],
                           "pomScope": "compile" if target else "runtime", "dependencies": sorted(actual_pom)})
            if record(directory / f"{name}-{version}-license.txt")["sha256"] != model["license"]["sha256"]:
                raise RuntimeError("Lyricist published license bytes differ")
            with zipfile.ZipFile(directory / f"{name}-{version}-sources.jar") as archive:
                sources = [{"member": item.filename, "size": item.file_size, "sha256": hashlib.sha256(archive.read(item)).hexdigest()}
                           for item in archive.infolist() if item.filename.endswith(".kt")]
                if sorted(sources, key=lambda item: item["member"]) != model["sourceMembers"]:
                    raise RuntimeError("Lyricist published source JAR differs from the complete original source set")
                license_member = "META-INF/licenses/" + module + "/" + Path(model["license"]["path"]).name
                if hashlib.sha256(archive.read(license_member)).hexdigest() != model["license"]["sha256"]:
                    raise RuntimeError("Lyricist source JAR license differs")
            stage_name = "metadata" if not target else "native-x64" if target == "ohos_x64" else "native-arm64"
            compiled = next(item for item in stages[stage_name]["modules"] if item["module"] == module)
            if target:
                klib = directory / f"{name}-{version}.klib"
                with zipfile.ZipFile(klib) as archive:
                    for producer in compiled["producers"]:
                        for item in producer["outputs"]:
                            path = Path(item["path"])
                            if "/default/" in path.as_posix():
                                member = "default/" + path.as_posix().split("/default/", 1)[1]
                                if hashlib.sha256(archive.read(member)).hexdigest() != item["sha256"]:
                                    raise RuntimeError("Published Klib differs from real Native compiler members")
                            elif path.suffix == ".klib" and record(klib)["sha256"] != item["sha256"]:
                                raise RuntimeError("Published Klib differs from real Native compiler output")
            else:
                jar = next(item for producer in compiled["producers"] if producer["task"].endswith(":allMetadataJar") for item in producer["outputs"] if item["path"].endswith(".jar"))
                if record(directory / f"{name}-{version}.jar")["sha256"] != jar["sha256"]:
                    raise RuntimeError("Published metadata differs from standard KGP output")
                for key, classifier in (("sources", "upstream-sources.jar"), ("pom", "upstream-pom.xml"), ("gmm", "upstream-module.json"), ("upstreamBuild", "upstream-build.gradle.kts")):
                    if record(directory / f"{name}-{version}-{classifier}")["sha256"] != model[key]["sha256"]:
                        raise RuntimeError("Published original classifier differs")
            rows.extend(record(path) for path in sorted(directory.iterdir()) if path.is_file())
    signatures = [verify_api(value, stage, native_home) for stage in ("native-x64", "native-arm64")]
    result = {"status": "PUBLISHED_COMPLETE_LYRICIST_BYTES_VERIFIED_RUNTIME_NOT_RUN", **value, "files": rows,
              "compilations": [record(Path(value["work"]) / "evidence" / (stage + ".json")) for stage in stages],
              "dependencyScopes": scopes, "nativeSignatures": signatures, "runtime": "NOT_RUN", "armRuntime": "NOT_RUN"}
    write(Path(value["work"]) / "publication.json", result, exclusive=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("Plan", "Prepare", *TASKS, "Verify"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--tooling", type=Path)
    parser.add_argument("--native-home", type=Path)
    args = parser.parse_args()
    work, repository = output_roots(args)
    prepared = work / "prepared.json"
    tooling = Path(args.tooling).resolve() if args.tooling else (work / "tooling" / TOOL_REVISION).resolve()
    value = identity(args, tooling, (work, repository))
    model = load(HERE / "source-inputs.json")
    provenance = {"fingerprint": value["fingerprint"], "sourceCommit": value["sourceCommit"],
                  "upstream": model["upstream"], "group": GROUP, "modules": list(MODULES), "version": value["version"],
                  "toolingCommit": TOOL_REVISION, "sources": value["sources"], "recipe": value["recipe"],
                  "sourceInputs": model["modules"], "runtime": "NOT_RUN"}
    if args.stage == "Plan":
        print(json.dumps({"stage": "Plan", **value, "recipeCommitCheck": "NOT_RUN", "build": "NOT_RUN", "runtime": "NOT_RUN"}))
        return
    verify_recipe_commit(args.source_sha, value["recipe"])
    fresh = args.stage == "Prepare" and not prepared.exists()
    if fresh:
        for output in (work, repository):
            if output.exists() and (not output.is_dir() or any(output.iterdir())):
                raise RuntimeError("Fresh work and staging must be empty: " + str(output))
        work.mkdir(parents=True, exist_ok=True)
        checked_tools(work, args.tooling)
        members = []
        for source in value["sources"]:
            data = git_bytes(value["sourceCommit"], source["gitPath"])
            if len(data) != source["size"] or digest(data) != source["sha256"]:
                raise RuntimeError("Fork Git source bytes changed: " + source["member"])
            target = work / "sources" / source["module"] / Path(*PurePosixPath(source["member"]).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with physical(target).open("xb") as stream:
                stream.write(data)
            members.append({"module": source["module"], "member": source["member"], **record(target)})
        write(work / "source-manifest.json", {"fingerprint": value["fingerprint"], "members": members,
              "sourceCount": 2, "moduleSourceCounts": {m: 1 for m in MODULES},
              "sourceSetSha256": {m: model["modules"][m]["sourceSetSha256"] for m in MODULES},
              "publicApi": {m: model["modules"][m]["publicApi"] for m in MODULES}}, exclusive=True)
        write(work / "provenance.json", provenance, exclusive=True)
        write(prepared, value, exclusive=True)
    if not prepared.is_file() or load(prepared) != value or load(work / "provenance.json") != provenance:
        raise RuntimeError("Work identity or provenance differs; preserve this work and use a fresh directory")
    tools = checked_tools(work, args.tooling)
    manifest = load(work / "source-manifest.json")
    if manifest["fingerprint"] != value["fingerprint"] or manifest["sourceSetSha256"] != {m: model["modules"][m]["sourceSetSha256"] for m in MODULES}:
        raise RuntimeError("Prepared source manifest differs")
    for source in value["sources"]:
        path = work / "sources" / source["module"] / Path(*PurePosixPath(source["member"]).parts)
        actual = record(path)
        if (actual["size"], actual["sha256"]) != (source["size"], source["sha256"]):
            raise RuntimeError("Prepared source bytes differ: " + source["member"])
    if args.stage == "Prepare":
        print(json.dumps({"work": str(work), "toolingHome": str(tools), "fingerprint": value["fingerprint"], "version": value["version"]}))
        return
    check_native(args.native_home)
    if args.stage == "Verify":
        print(json.dumps(verify_publication(value, args.native_home)))
        return
    if args.stage != "Tasks":
        discovery = load(work / "task-discovery.json")
        if discovery["fingerprint"] != value["fingerprint"] or record(discovery["stdout"]["path"]) != discovery["stdout"]:
            raise RuntimeError("Task discovery differs")
    if args.stage not in ("Tasks", "Resolve"):
        if load(work / "evidence/resolve.json")["fingerprint"] != value["fingerprint"]:
            raise RuntimeError("Resolve belongs to another recipe")
        for label, stage, target in COMPILES:
            if label == args.stage:
                break
            verify_stage(value, stage)
    if args.stage == "Publish":
        for stage in ("native-x64", "native-arm64"):
            verify_api(value, stage, args.native_home)
        if repository.exists() and (not repository.is_dir() or any(repository.iterdir())):
            raise RuntimeError("Staging is not empty; refusing to overwrite existing files")
    evidence_name = {"Tasks": "task-discovery.json", "Resolve": "evidence/resolve.json",
                     "Metadata": "evidence/metadata.json", "NativeX64": "evidence/native-x64.json",
                     "NativeArm64": "evidence/native-arm64.json"}.get(args.stage)
    if evidence_name and (work / evidence_name).exists():
        raise RuntimeError("Stage evidence already exists; preserve it and use a fresh work directory")
    api_evidence = {"NativeX64": "evidence/native-x64-api.json", "NativeArm64": "evidence/native-arm64-api.json"}.get(args.stage)
    if api_evidence and (work / api_evidence).exists():
        raise RuntimeError("Native API evidence already exists; preserve it and use a fresh work directory")
    wrapper = ["cmd.exe", "/d", "/c", str(HERE / "gradlew.bat")] if os.name == "nt" else ["sh", str(HERE / "gradlew")]
    argv = [*wrapper, "-p", str(HERE), "--project-cache-dir", str(work / "project-cache"),
            "-PportWork=" + str(work), "-PportTooling=" + str(tools), "-PportFingerprint=" + value["fingerprint"], "-PportVersion=" + value["version"],
            "-PportRepository=" + str(repository), "-Pkotlin.project.persistent.dir=" + str(work / "kotlin-cache"),
            "-Pkotlin.native.home=" + str(args.native_home.resolve()), "--console=plain", "--stacktrace", *TASKS[args.stage]]
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout, stderr = logs / (args.stage + ".stdout.log"), logs / (args.stage + ".stderr.log")
    with physical(stdout).open("xb") as out, physical(stderr).open("xb") as err:
        run = subprocess.run(argv, env=safe_environment(work), stdout=out, stderr=err)
    if run.returncode:
        print(physical(stderr).read_text(encoding="utf-8", errors="replace")[-16384:], file=sys.stderr)
        raise RuntimeError("Gradle " + args.stage + " failed with exit " + str(run.returncode))
    if args.stage == "Tasks":
        names = {name.lstrip(":") for name in re.findall(r"^\s*([\w:-]+)(?:\s+-|\s*$)", physical(stdout).read_text(encoding="utf-8", errors="replace"), re.MULTILINE)}
        if not REQUIRED.issubset(names):
            raise RuntimeError("Required tasks absent from actual discovery: " + ", ".join(sorted(REQUIRED - names)))
        write(work / "task-discovery.json", {"fingerprint": value["fingerprint"], "required": sorted(REQUIRED), "stdout": record(stdout)}, exclusive=True)
    for label, stage, target in COMPILES:
        if label == args.stage:
            verify_stage(value, stage)
            if target is not None:
                signature_stage(value, stage, args.run_attempt, args.native_home)
    print(json.dumps({"stage": args.stage, "exitCode": run.returncode, "fingerprint": value["fingerprint"], "stdout": record(stdout)}))


if __name__ == "__main__":
    main()
