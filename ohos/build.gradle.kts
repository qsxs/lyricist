import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.security.MessageDigest
import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.gradle.api.artifacts.component.ProjectComponentIdentifier
import org.gradle.api.artifacts.result.ResolvedDependencyResult
import org.gradle.api.artifacts.result.UnresolvedDependencyResult
import org.gradle.api.publish.PublishingExtension
import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.tasks.PublishToMavenRepository
import org.gradle.jvm.tasks.Jar
import org.jetbrains.kotlin.gradle.dsl.KotlinMultiplatformExtension
import org.jetbrains.kotlin.gradle.tasks.KotlinNativeCompile

plugins {
    kotlin("multiplatform") apply false
    kotlin("plugin.compose") apply false
    id("org.jetbrains.compose") apply false
}

fun readMap(path: File) = (JsonSlurper().parse(path) as Map<*, *>).entries.associate { it.key.toString() to it.value }
fun asMap(value: Any?) = (value as Map<*, *>).entries.associate { it.key.toString() to requireNotNull(it.value) }
fun sha(path: File) = MessageDigest.getInstance("SHA-256").digest(path.readBytes()).joinToString("") { "%02x".format(it) }
fun leaves(path: File): List<File> = if (path.isDirectory) path.walkTopDown().filter(File::isFile).toList() else listOf(path)
fun records(files: Iterable<File>) = files.filter(File::isFile).distinct().sortedBy { it.path }.map {
    mapOf("path" to it.absolutePath, "size" to it.length(), "sha256" to sha(it))
}

val config = readMap(file("port-config.json"))
val modules = asMap(config.getValue("modules"))
val sourceInputs = asMap(readMap(file("source-inputs.json")).getValue("modules"))
val pins = readMap(file("dependency-pins.json"))
val metadataPins = readMap(file("metadata-pins.json"))
check(gradle.gradleVersion == config.getValue("gradle") && System.getProperty("java.specification.version") == "17")
val portVersion = providers.gradleProperty("portVersion").get()
val portRepository = file(providers.gradleProperty("portRepository").get()).canonicalFile
val toolRoot = file(providers.gradleProperty("portTooling").get()).resolve("toolchains/kotlin-native/2.4.20-0.1.0-08")
val metadataTools = readMap(toolRoot.resolve("metadata-toolchain.json"))
val commonizerTools = readMap(toolRoot.resolve("commonizer-toolchain.json"))
val nativeTools = readMap(toolRoot.resolve("native-compiler-toolchain.json"))
val commonizerPins = asMap(metadataTools.getValue("launcherPins")) + asMap(commonizerTools.getValue("pins"))
val commonizerArtifacts = asMap(metadataTools.getValue("launcherArtifacts")) + asMap(commonizerTools.getValue("artifacts"))
val nativePins = asMap(nativeTools.getValue("pins"))
val nativeArtifacts = asMap(nativeTools.getValue("artifacts"))
val compilerPlugins = readMap(file("compiler-plugin-toolchain.json"))
val fingerprint = providers.gradleProperty("portFingerprint").get()
val work = file(providers.gradleProperty("portWork").get()).canonicalFile
check(fingerprint.matches(Regex("[0-9a-f]{64}")))
val manifestFile = work.resolve("source-manifest.json")
check(readMap(manifestFile)["fingerprint"] == fingerprint)
val localGavs = modules.map { (name, value) ->
    val model = asMap(value)
    "${model.getValue("group")}:$name:$portVersion"
}.toSet()
layout.buildDirectory.set(work.resolve("build/root"))

fun evidence(name: String, value: Any) {
    val target = work.resolve("evidence/$name.json")
    target.parentFile.mkdirs()
    target.writeText(JsonOutput.prettyPrint(JsonOutput.toJson(value)) + "\n")
}

subprojects {
    val module = name
    val model = asMap(modules.getValue(name))
    group = model.getValue("group").toString()
    version = portVersion
    layout.buildDirectory.set(work.resolve("build/$name"))
    plugins.withId("org.jetbrains.kotlin.multiplatform") {
        extensions.configure<KotlinMultiplatformExtension> {
            if (model["explicitApi"] == "Strict") explicitApi()
            ohosX64()
            ohosArm64()
            sourceSets {
                val commonMain by getting {
                    kotlin.setSrcDirs(listOf(work.resolve("sources/${module}/commonMain")))
                    dependencies {
                        when (module) {
                            "lyricist" -> {
                                api(project(":lyricist-core"))
                                implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:${pins.getValue("org.jetbrains.kotlinx:kotlinx-coroutines-core")}")
                                compileOnly("org.jetbrains.compose.runtime:runtime:${pins.getValue("org.jetbrains.compose.runtime:runtime")}")
                                compileOnly("org.jetbrains.compose.ui:ui:${pins.getValue("org.jetbrains.compose.ui:ui")}")
                            }
                            "lyricist-core" -> implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:${pins.getValue("org.jetbrains.kotlinx:kotlinx-coroutines-core")}")
                        }
                    }
                }
                listOf("ohosX64Main", "ohosArm64Main").forEach {
                    getByName(it).dependsOn(commonMain)
                    getByName(it).kotlin.setSrcDirs(emptyList<File>())
                }
                matching { it.name.endsWith("Test") }.configureEach { kotlin.setSrcDirs(emptyList<File>()) }
            }
        }
    }
    configurations.configureEach {
        if (name == nativeTools["configuration"] || name == commonizerTools["configuration"]) {
            val fixedPins = if (name == nativeTools["configuration"]) nativePins else commonizerPins
            resolutionStrategy.eachDependency {
                val ga = "${requested.group}:${requested.name}"
                val fixed = fixedPins[ga]?.toString() ?: error("Unpinned Lyricist compiler host: $ga")
                if (requested.version != fixed) useVersion(fixed)
            }
        }
        val metadata = name.endsWith("ResolvableDependenciesMetadata") || name == "allSourceSetsCompileDependenciesMetadata"
        val compile = metadata || name.endsWith("CompileKlibraries") || name.endsWith("CompilationDependenciesMetadata")
        if (compile && !name.contains("Test", ignoreCase = true)) resolutionStrategy.eachDependency {
            val ga = "${requested.group}:${requested.name}"
            if ("$ga:${requested.version}" !in localGavs) {
                val fixed = (if (metadata) metadataPins[ga] ?: pins[ga] else pins[ga])?.toString()
                    ?: error("Unpinned Lyricist dependency: $requested")
                if (requested.version != fixed) useVersion(fixed)
            }
        }
    }
    plugins.withId("maven-publish") {
        extensions.configure<PublishingExtension> {
            repositories { maven { name = "Port"; url = uri(portRepository) } }
            publications.withType<MavenPublication>().configureEach {
                artifact(rootProject.file(model.getValue("licensePath").toString())) { classifier = "license"; extension = "txt" }
                artifact(work.resolve("provenance.json")) { classifier = "provenance"; extension = "json" }
                if (name == "kotlinMultiplatform") {
                    val original = asMap(sourceInputs.getValue(module))
                    listOf("sources" to ("upstream-sources" to "jar"), "pom" to ("upstream-pom" to "xml"),
                        "gmm" to ("upstream-module" to "json"), "upstreamBuild" to ("upstream-build" to "gradle.kts")).forEach { (key, format) ->
                        artifact(rootProject.file(asMap(original.getValue(key)).getValue("path").toString())) {
                            classifier = format.first
                            extension = format.second
                        }
                    }
                }
                pom {
                    name.set(module)
                    description.set("Complete unmodified ${model.getValue("upstreamVersion")} sources for CPF OHOS dual ABI")
                    licenses { license { name.set(model.getValue("license").toString()); url.set(model.getValue("licenseUrl").toString()) } }
                    scm { url.set(model.getValue("repository").toString()) }
                    developers { developer { id.set("adrielcafe"); name.set("Adriel Cafe"); url.set("https://github.com/adrielcafe/") } }
                }
            }
        }
    }
    tasks.withType<PublishToMavenRepository>().configureEach {
        doFirst {
            check(readMap(work.resolve("provenance.json"))["fingerprint"] == fingerprint)
            val original = asMap(sourceInputs.getValue(module))
            listOf("sources", "pom", "gmm", "upstreamBuild", "license", "nativeReferencePom", "nativeReferenceGmm").forEach { key ->
                val item = asMap(original.getValue(key))
                check(sha(rootProject.file(item.getValue("path").toString())) == item.getValue("sha256"))
            }
            listOf("metadata", "native-x64", "native-arm64").forEach {
                check(readMap(work.resolve("evidence/$it.json"))["fingerprint"] == fingerprint)
            }
        }
    }
    tasks.withType<Jar>().configureEach {
        isPreserveFileTimestamps = false
        isReproducibleFileOrder = true
        from(rootProject.file(model.getValue("licensePath").toString())) { into("META-INF/licenses/${module}") }
        from(manifestFile) { into("META-INF/upstream") }
    }
}

fun checkedTools(child: Project, name: String, fixed: Map<String, Any>, expected: Map<String, Any>): Map<String, Any> {
    val configuration = child.configurations.getByName(name)
    val artifacts = configuration.resolvedConfiguration.resolvedArtifacts.map { artifact ->
        val id = artifact.moduleVersion.id
        val ga = "${id.group}:${id.name}"
        check(fixed[ga] == id.version) { "Host tool version differs: $id" }
        val item = asMap(expected.getValue(ga))
        check(sha(artifact.file) == item["sha256"] && artifact.file.length() == (item.getValue("size") as Number).toLong())
        mapOf("gav" to id.toString(), "files" to records(listOf(artifact.file)))
    }
    check(artifacts.map { it.getValue("gav") }.toSet() == fixed.map { "${it.key}:${it.value}" }.toSet())
    return mapOf("configuration" to name, "artifacts" to artifacts)
}
fun toolchain() = mapOf("hostTools" to subprojects.associate { child -> child.name to mapOf(
    "commonizer" to checkedTools(child, commonizerTools.getValue("configuration").toString(), commonizerPins, commonizerArtifacts),
    "nativeCompiler" to checkedTools(child, nativeTools.getValue("configuration").toString(), nativePins, nativeArtifacts)) },
    "gradle" to gradle.gradleVersion, "javaVersion" to System.getProperty("java.version"),
    "javaRuntimeVersion" to System.getProperty("java.runtime.version"), "javaHome" to System.getProperty("java.home"),
    "nativeHome" to providers.gradleProperty("kotlin.native.home").get(), "konanDataDir" to System.getenv("KONAN_DATA_DIR"),
    "gradleUserHome" to gradle.gradleUserHomeDir.absolutePath, "temp" to System.getenv("TEMP"),
    "plugins" to buildscript.configurations.getByName("classpath").resolvedConfiguration.resolvedArtifacts.map {
        mapOf("component" to it.moduleVersion.id.toString(), "files" to records(listOf(it.file)))
    })

fun resolution() = subprojects.flatMap { child ->
    listOf("commonMainResolvableDependenciesMetadata", "ohosX64CompileKlibraries", "ohosArm64CompileKlibraries").map { name ->
        val configuration = child.configurations.getByName(name)
        val metadata = name.endsWith("ResolvableDependenciesMetadata")
        mapOf("module" to child.name, "configuration" to name, "edges" to configuration.incoming.resolutionResult.allDependencies.map { edge ->
            if (edge !is ResolvedDependencyResult) throw GradleException("Unresolved Lyricist dependency: $edge", (edge as? UnresolvedDependencyResult)?.failure)
            mapOf("from" to edge.from.id.displayName, "requested" to edge.requested.displayName, "selected" to edge.selected.id.displayName)
        }, "artifacts" to configuration.incoming.artifacts.artifacts.map { artifact ->
            val id = artifact.id.componentIdentifier
            val attrs = artifact.variant.attributes.keySet().associate { it.name to artifact.variant.attributes.getAttribute(it).toString() }
            if (id is ModuleComponentIdentifier) {
                val ga = "${id.group}:${id.module}"
                check((if (metadata) metadataPins[ga] ?: pins[ga] else pins[ga]) == id.version)
                if (metadataPins.containsKey(ga)) {
                    val common = attrs["org.gradle.usage"] == "kotlin-metadata" && attrs["org.jetbrains.kotlin.platform.type"] == "common"
                    val javaAlias = ga == "androidx.arch.core:core-common" && id.version == "2.2.0" &&
                        attrs["org.gradle.usage"] in listOf("java-api", "java-runtime") && attrs["org.jetbrains.kotlin.platform.type"] in listOf(null, "jvm")
                    check(metadata && (common || javaAlias) && attrs["org.jetbrains.kotlin.native.target"] == null)
                }
            } else {
                check(id is ProjectComponentIdentifier && id.projectPath.removePrefix(":") in modules.keys)
            }
            mapOf("component" to id.displayName, "attributes" to attrs, "files" to records(leaves(artifact.file)))
        })
    }
}

tasks.register("resolveLyricist") {
    doLast {
        check(gradle.gradleVersion == config.getValue("gradle") && System.getProperty("java.specification.version") == "17")
        subprojects.forEach { child ->
            listOf("compileCommonMainKotlinMetadata", "allMetadataJar", "compileKotlinOhosX64", "compileKotlinOhosArm64").forEach { child.tasks.getByName(it) }
        }
        evidence("resolve", mapOf("fingerprint" to fingerprint, "toolchain" to toolchain(), "configurations" to resolution()))
    }
}

fun recordStage(name: String, target: String?) {
    tasks.register(name) {
        val compilerName = if (target == null) "compileCommonMainKotlinMetadata" else "compileKotlin$target"
        val producerPaths = subprojects.flatMap { child ->
            listOf("${child.path}:$compilerName") + if (target == null) listOf("${child.path}:allMetadataJar") else emptyList()
        }
        dependsOn(producerPaths)
        outputs.upToDateWhen { false }
        doLast {
            val stage = if (target == null) "metadata" else if (target == "OhosX64") "native-x64" else "native-arm64"
            val compiled = subprojects.map { child ->
                val original = asMap(sourceInputs.getValue(child.name))
                val compiler = child.tasks.getByName(compilerName) as KotlinNativeCompile
                val sources = compiler.sources.files.filter { it.isFile && it.extension == "kt" }.map { it.canonicalFile }.toSet()
                val pluginFiles = compiler.compilerPluginClasspath?.files.orEmpty()
                val pluginId = "org.jetbrains.kotlin:kotlin-compose-compiler-plugin-embeddable"
                val pluginPins = asMap(compilerPlugins.getValue("pins"))
                val pluginArtifacts = asMap(compilerPlugins.getValue("artifacts"))
                val expectedPlugins = if (child.name == "lyricist") {
                    check(pluginPins[pluginId] == config["kotlin"])
                    val item = asMap(pluginArtifacts.getValue(pluginId))
                    setOf((item.getValue("size") as Number).toLong() to item.getValue("sha256").toString())
                } else emptySet()
                check(pluginFiles.size == expectedPlugins.size && pluginFiles.map { it.length() to sha(it) }.toSet() == expectedPlugins) {
                    "Lyricist compiler plugin set differs: ${child.name}"
                }
                val expected = (original.getValue("sourceMembers") as List<*>).map(::asMap).associate {
                    work.resolve("sources/${child.name}/${it.getValue("member")}").canonicalFile to it.getValue("sha256").toString()
                }
                check(sources == expected.keys && expected.all { (path, hash) -> sha(path) == hash }) {
                    "Complete original Lyricist source set differs: ${child.name}"
                }
                val producers = listOf(child.tasks.getByName(compilerName)) + if (target == null) listOf(child.tasks.getByName("allMetadataJar")) else emptyList()
                producers.forEach { producer ->
                    val state = producer.state
                    check(state.executed && producer.enabled && !state.noSource && state.failure == null &&
                        state.skipMessage in listOf(null, "UP-TO-DATE", "FROM-CACHE")) { "Lyricist producer did not execute: ${producer.path}" }
                    check(records(producer.outputs.files.files.flatMap(::leaves)).any { (it.getValue("size") as Long) > 0 })
                }
                mapOf("module" to child.name, "sourceCount" to sources.size, "sourceSetSha256" to original.getValue("sourceSetSha256"),
                    "sources" to records(sources), "publicApi" to original.getValue("publicApi"),
                    "explicitApi" to child.extensions.getByType<KotlinMultiplatformExtension>().explicitApi?.name,
                    "sourceDependencies" to listOf("api" to "commonMainApi", "implementation" to "commonMainImplementation", "compileOnly" to "commonMainCompileOnly").associate { (scope, configuration) ->
                        scope to child.configurations.getByName(configuration).dependencies.map { "${it.group}:${it.name}" }.filter { it != "org.jetbrains.kotlin:kotlin-stdlib" }
                    }, "compilerPlugins" to records(compiler.compilerPluginClasspath?.files.orEmpty()), "pluginOptions" to compiler.compilerPluginCommandLine,
                    "languageOptions" to mapOf("languageVersion" to compiler.compilerOptions.languageVersion.orNull?.version,
                        "apiVersion" to compiler.compilerOptions.apiVersion.orNull?.version, "optIns" to compiler.compilerOptions.optIn.get().sorted(),
                        "freeCompilerArgs" to compiler.compilerOptions.freeCompilerArgs.get()),
                    "metadataLibraries" to if (target == null) compiler.libraries.files.sortedBy { it.path }.map { mapOf("path" to it.absolutePath, "files" to records(leaves(it))) } else null,
                    "producers" to producers.map { mapOf("task" to it.path, "outputs" to records(it.outputs.files.files.flatMap(::leaves))) })
            }
            evidence(stage, mapOf("fingerprint" to fingerprint, "stage" to stage, "sourceManifest" to records(listOf(manifestFile)).single(),
                "modules" to compiled, "toolchain" to toolchain(), "configurations" to resolution(), "runtime" to "NOT_RUN"))
        }
    }
}
recordStage("recordLyricistMetadata", null)
recordStage("recordLyricistX64", "OhosX64")
recordStage("recordLyricistArm64", "OhosArm64")
tasks.register("publishLyricist") { dependsOn(subprojects.map { "${it.path}:publishAllPublicationsToPortRepository" }) }
