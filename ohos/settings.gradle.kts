import groovy.json.JsonSlurper

pluginManagement {
    repositories {
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-develop/")
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/")
        gradlePluginPortal()
        mavenCentral()
    }
    plugins {
        id("org.jetbrains.kotlin.multiplatform") version "2.4.20-0.1.0-08"
        id("org.jetbrains.kotlin.plugin.compose") version "2.4.20-0.1.0-08"
        id("org.jetbrains.compose") version "1.12.0-0.1.0-11"
    }
}
val pins = JsonSlurper().parse(file("dependency-pins.json")) as Map<*, *>
val metadataPins = if (file("metadata-pins.json").isFile) JsonSlurper().parse(file("metadata-pins.json")) as Map<*, *> else emptyMap<Any, Any>()
val toolRoot = file(providers.gradleProperty("portTooling").get()).resolve("toolchains/kotlin-native/2.4.20-0.1.0-08")
fun tools(name: String) = JsonSlurper().parse(toolRoot.resolve(name)) as Map<*, *>
val launcher = tools("metadata-toolchain.json")["launcherPins"] as Map<*, *>
val commonizer = tools("commonizer-toolchain.json")["pins"] as Map<*, *>
val host = tools("native-compiler-toolchain.json")["pins"] as Map<*, *>
val compilerPlugins = (JsonSlurper().parse(file("compiler-plugin-toolchain.json")) as Map<*, *>)["pins"] as Map<*, *>
val allowed = pins.entries + metadataPins.entries + launcher.entries + commonizer.entries + host.entries + compilerPlugins.entries
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google { content { metadataPins.forEach { (ga, version) ->
            val parts = ga.toString().split(":")
            if (parts[0].startsWith("androidx.")) includeVersion(parts[0], parts[1], version.toString())
        } } }
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-develop/") {
            content { allowed.forEach { (ga, version) ->
                val parts = ga.toString().split(":")
                includeVersion(parts[0], parts[1], version.toString())
            } }
        }
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/") {
            content { allowed.forEach { (ga, version) ->
                val parts = ga.toString().split(":")
                includeVersion(parts[0], parts[1], version.toString())
            } }
        }
        mavenCentral()
    }
}
rootProject.name = "lyricist-ohos"
include(":lyricist-core", ":lyricist")
