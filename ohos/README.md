# Lyricist 1.8.0 OHOS 独立 producer

上游 [adrielcafe/lyricist 1.8.0](https://github.com/adrielcafe/lyricist/commit/89b8b6821b288164f71e5196f33d8f706c758fae) 的 MIT 许可及作者 Adriel Cafe 保留在配方和发布 POM。`lyricist` 和 `lyricist-core` 各保留完整一份 common 原源、所有原 API 与 `explicitApi Strict`；官方 sources JAR、原 POM/GMM、原构建声明、iOS 参考 POM/GMM 与 LICENSE 原件随配方保留。

原依赖 scope 不变：`lyricist` 对 `lyricist-core` 为 `api`、coroutines 为 `implementation`、Compose runtime 和 ui 为 `compileOnly`；`lyricist-core` 的 coroutines 为 `implementation`。`LanguageTag` 是 typealias，不是运行时类；`LyricistState` 的构造函数为 internal；本次 `.08` 编译 metadata 中生成的 `copy` 为 public。Native 阶段记录完整 metadata、声明 IR 和 metadata dump，供检查注解、`rememberStrings`、`strings` 以及其他上游声明。

独立 group 为 `io.github.qsxs.cpf.lyricist`，两个模块为 `lyricist` 和 `lyricist-core`；本次 preview 版本为 `1.8.0-cpf11-preview-<sourceSHA12>-<runID>-<attempt>`。使用 KGP、Kotlin/Native 和 Compose compiler `2.4.20-0.1.0-08`、Compose `1.12.0-0.1.0-11`、coroutines `1.11.0-0.1.0-07`、Gradle 9.5.0 与 JDK 17；先执行标准 KGP metadata，不预设替代入口。

Prepare 从指定 Fork 完整提交读取 Git blob，将两份源文件和 LICENSE 与固定上游原件逐字节核对，再复制到独立 work。Windows 工作树可能受 `core.autocrlf` 改变，工作树字节不作为原始来源一致性的证明。配方与源码均须属于同一完整提交，预览版本绑定该提交与运行 ID/attempt。

只读 Plan 示例（路径须为相互分离且位于 Fork checkout 外的独立目录）：

```sh
python -B ohos/LyricistPort.py Plan --source-sha "$SOURCE_SHA" --run-id "$RUN_ID" --run-attempt "$RUN_ATTEMPT" --work "$WORK_DIR" --repository "$STAGING_DIR"
```

Plan 不创建目录、不下载或构建，且报告 `recipeCommitCheck: NOT_RUN`；Prepare 前须确保完整 `SOURCE_SHA` 包含 `ohos/` 配方。首次 Prepare 要求 work 与 staging 不存在或为空，失败现场不自动清理；已有准备目录只能在身份一致时复用。`--tooling` 可指定经逐文件校验的共享工具 A `d0cff88fd1ea2e4f08bb4409f1c802cca0c7f285`，不指定时获取该固定归档。后续命令还须以 `--native-home` 显式指定官方 `.08` K/N，依次执行 `Tasks`、`Resolve`、`Metadata`、`NativeX64`、`NativeArm64`、`Publish`、`Verify`。Gradle、K/N 数据及临时目录隔离在 work，发布仅写入 staging；不读取 App、共享 helper 或本机绝对路径配置。

阶段记录编译源、固定依赖、编译器插件、标准 metadata 与双 Native 输出；Verify 核对两模块根/双 ABI GMM、POM scope、作者、原件、源码、provenance、Klib 与签名。云端 producer 和 Gradle 9.5 匿名公开消费编译已独立核验；完整源码与编译不代表运行验收。


## Lyricist 与 Lyricist-core

[Fork source `fad3d9091c75`](https://github.com/qsxs/lyricist/commit/fad3d9091c75927320da3511350202e681269b2a) 保留上游 Lyricist 1.8.0 两模块各一份完整 common 原源、MIT 许可与作者；`lyricist` 对 `lyricist-core` 为 `api`，coroutines 为 `implementation`，Compose runtime/ui 为 `compileOnly`；`lyricist-core` 的 coroutines 为 `implementation`，两模块保留 `explicitApi Strict`。

两个根 GAV 为 `io.github.qsxs.cpf.lyricist:lyricist:1.8.0-cpf11-preview-fad3d9091c75-40827525373-1` 和 `io.github.qsxs.cpf.lyricist:lyricist-core:1.8.0-cpf11-preview-fad3d9091c75-40827525373-1`；公开 Maven 根 `https://qsxs.github.io/cpf-maven/`（[lyricist POM](https://qsxs.github.io/cpf-maven/io/github/qsxs/cpf/lyricist/lyricist/1.8.0-cpf11-preview-fad3d9091c75-40827525373-1/lyricist-1.8.0-cpf11-preview-fad3d9091c75-40827525373-1.pom)、[core POM](https://qsxs.github.io/cpf-maven/io/github/qsxs/cpf/lyricist/lyricist-core/1.8.0-cpf11-preview-fad3d9091c75-40827525373-1/lyricist-core-1.8.0-cpf11-preview-fad3d9091c75-40827525373-1.pom)）。

[producer 40827525373/1](https://github.com/qsxs/cpf-port-tooling/actions/runs/40827525373/attempts/1) 使用工作流 `a2e3e537cfeffea4f9e63c70b190e46daddbd1ac`、共享工具锁 `d0cff88fd1ea2e4f08bb4409f1c802cca0c7f285` 完成 metadata 和 OHOS x64/arm64 生产及独立 staging 核验。Metadata 编译器库与 core 输出记录相符；Native 解析配置与 core 输出记录相符，但 Native 编译器 libraries、完整 compiler argv、taskDependencies 和 runner 物理输入未上传，不能在本机确认实际 Native 编译器输入或重哈这些路径。

[Pages 提交 `d4aeac15ad29`](https://github.com/qsxs/cpf-maven/commit/d4aeac15ad29f85497a733b133f12f2164322b44) 的 270 个文件、354,689 字节在修正匿名批次中与 staging 全部对齐，该批 270/270 首次请求成功；首轮将分隔符误计入响应 body，统计无效。 [consumer 40829692921/1](https://github.com/qsxs/cpf-port-tooling/actions/runs/40829692921/attempts/1) 使用工作流 `469293ce18774921a87627e756a617bff115b368` 和 Gradle 9.5.0，完成 `@LyricistStrings`、`rememberStrings`、语言 tag 和 `Lyricist.strings` 探针的 metadata、OHOS x64 与 arm64 真实编译；三个配置中 `lyricist` 通过 `api` 解析 `lyricist-core`，Compose runtime/ui 由 consumer 显式提供，不作为库的传递依赖。44 条公开 HTTP 记录与 staging 对齐，其中仅 38 条响应 body 随证据上传并可在本机复哈；四个 Native Klib 输入记录与 Pages/staging SHA-256 一致。两个 metadata trace 各有三个成员，但转换后的 metadata Klib 与 runner 物理输入未上传，无法本机独立重哈。

Gradle 8 消费、link、runtime、设备和 Android/iOS 回归未验证，App 引用尚未切换。Wheel 的 producer、Pages 及 Gradle 9.5 三目标公开消费编译已验；[consumer 40841841344/1](https://github.com/qsxs/cpf-port-tooling/actions/runs/40841841344/attempts/1) 完成业务探针编译，未获授权移除其 Lyricist 依赖。
