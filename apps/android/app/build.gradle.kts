// M12-01 Android App Shell：app 模块。
// 第一切片只做壳与认证/API 基础；不接入考试、语音、检索、治理业务。
import java.io.File
import java.io.IOException
import java.nio.file.InvalidPathException
import java.nio.file.Path
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// M14-171A：release 签名就绪（显式 opt-in，外部材料绝不落库/回退/打印）。
// 契约见 docs/MOBILE_DISTRIBUTION.md §1 与 tools/android_release/preflight.py。
// 四项外部输入（任一来源出现即视为 opt-in，逐项合并、env 优先于文件）：
//   - 环境变量 AIOS_ANDROID_KEYSTORE_PATH / _STORE_PASSWORD / _KEY_ALIAS / _KEY_PASSWORD
//   - 或 AIOS_ANDROID_SIGNING_PROPERTIES 指向的仓库外 properties 文件
//     （键：keystore.path / keystore.storePassword / keystore.keyAlias / keystore.keyPassword）
//     两个路径输入（properties 文件与 keystore）解析后必须位于仓库之外；
//     落入仓库内即 failClosedOutsideRepo 直接失败（M14-171B）。
// opt-in 后四项必须齐全：缺失即在配置阶段直接失败（不回退 debug 签名、
// 不静默降级）；完全无外部输入时 release 保持 unsigned（诚实默认，
// 产物为 app-release-unsigned.apk，/download 渠道继续 pending）。
// 错误信息只包含缺失的输入名，绝不包含任何值。
val releaseSigningInputs: Map<String, String>? = run {
    val signingEnvInputs = listOf(
        "AIOS_ANDROID_KEYSTORE_PATH" to "keystore.path",
        "AIOS_ANDROID_KEYSTORE_STORE_PASSWORD" to "keystore.storePassword",
        "AIOS_ANDROID_KEYSTORE_KEY_ALIAS" to "keystore.keyAlias",
        "AIOS_ANDROID_KEYSTORE_KEY_PASSWORD" to "keystore.keyPassword",
    )
    fun envOrNull(name: String): String? =
        providers.environmentVariable(name).orNull?.trim()?.takeIf { it.isNotEmpty() }

    // M14-171B：fail-closed 仓库边界守卫。文档契约（MOBILE_DISTRIBUTION.md §1）
    // 要求含密码的 properties 与 keystore 一律存放于仓库之外；本守卫把该要求
    // 变成配置期硬约束。锚点取 Gradle 根向上最近的 VCS 根（.git 文件或目录，
    // worktree 内为文件），使仓库根（而非仅 apps/android）都被覆盖；找不到
    // .git 时退回 rootProject.rootDir。输入路径用 real path 解析（跟随符号
    // 链接、规范大小写与分隔符）后做带分隔符的前缀判断，防止绝对/相对路径、
    // 大小写变体或符号链接把仓库内文件伪装成外部输入；解析失败（IOException）
    // 或非法路径（InvalidPathException，file.toPath() 可抛）均转换为不含路径
    // 值的 GradleException 直接失败（fail-closed；正常流程 isFile 门会先拦下
    // 不存在/非法的路径，此分支为纵深防御）。错误信息只含输入名，不含任何
    // 路径或密码值。tools/android_release/preflight.py 静态钉住本守卫不可被删除。
    val signingRepoAnchor: File = generateSequence(rootProject.rootDir) { it.parentFile }
        .firstOrNull { dir -> File(dir, ".git").exists() }
        ?: rootProject.rootDir
    fun failClosedOutsideRepo(rawPath: String, inputName: String) {
        fun realOrReject(what: String, resolve: () -> Path): Path = try {
            resolve()
        } catch (_: IOException) {
            throw GradleException(
                "M14-171A：无法解析${what}的真实路径，release 签名的仓库边界无法验证，" +
                    "配置直接失败（fail-closed，路径值不打印）。"
            )
        } catch (_: InvalidPathException) {
            throw GradleException(
                "M14-171A：${what}包含非法字符、无法转换为合法路径，release 签名的仓库边界" +
                    "无法验证，配置直接失败（fail-closed，路径值不打印）。"
            )
        }
        val repoText = realOrReject("仓库锚点") {
            signingRepoAnchor.toPath().toRealPath()
        }.toString().lowercase()
        val inputText = realOrReject(inputName) {
            File(rawPath).toPath().toRealPath()
        }.toString().lowercase()
        if (inputText == repoText || inputText.startsWith(repoText + File.separator)) {
            throw GradleException(
                "M14-171A：release 签名输入 $inputName 解析后位于仓库之内。" +
                    "外部签名材料（含密码的 properties 文件与 keystore）必须存放于仓库之外" +
                    "（路径值不打印）。"
            )
        }
    }

    val propsPath = envOrNull("AIOS_ANDROID_SIGNING_PROPERTIES")
    val fileInputs: Map<String, String> = if (propsPath != null) {
        val propsFile = File(propsPath)
        if (!propsFile.isFile) {
            throw GradleException(
                "M14-171A：release 签名 opt-in 已触发，但 AIOS_ANDROID_SIGNING_PROPERTIES " +
                    "指向的文件不存在或不可读（值不打印）。"
            )
        }
        failClosedOutsideRepo(propsPath, "AIOS_ANDROID_SIGNING_PROPERTIES")
        Properties().apply { propsFile.inputStream().use { load(it) } }
            .entries.associate { (key, value) -> key.toString() to value.toString().trim() }
            .filterValues { it.isNotEmpty() }
    } else {
        emptyMap()
    }

    val merged: Map<String, String?> = signingEnvInputs.associate { (envName, fileKey) ->
        envName to (envOrNull(envName) ?: fileInputs[fileKey])
    }
    val optedIn = propsPath != null || merged.values.any { it != null }
    if (!optedIn) {
        null
    } else {
        val missing = merged.filterValues { it == null }.keys.sorted()
        if (missing.isNotEmpty()) {
            throw GradleException(
                "M14-171A：release 签名输入不完整——四项外部输入必须同时存在" +
                    "（缺失：${missing.joinToString(", ")}）。不会回退 debug 签名，也不会静默降级为 unsigned。"
            )
        }
        val resolved = merged.mapValues { (_, value) -> value as String }
        if (!File(resolved.getValue("AIOS_ANDROID_KEYSTORE_PATH")).isFile) {
            throw GradleException(
                "M14-171A：release 签名 opt-in 已触发，但 AIOS_ANDROID_KEYSTORE_PATH " +
                    "指向的 keystore 文件不存在或不可读（值不打印）。"
            )
        }
        failClosedOutsideRepo(
            resolved.getValue("AIOS_ANDROID_KEYSTORE_PATH"),
            "AIOS_ANDROID_KEYSTORE_PATH",
        )
        resolved
    }
}

android {
    namespace = "com.ailearningos.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.ailearningos.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // release 强制 HTTPS 的开关面：baseUrl 策略只读这一个字段（便于 JVM 测试注入）。
        buildConfigField("boolean", "ALLOW_INSECURE_HTTP", "false")
    }

    signingConfigs {
        // M14-171A：仅当四项外部输入全部就绪时才创建 release 签名配置；
        // 密码/路径一律来自上面解析的外部输入，本文件不出现任何字面量
        // （tools/android_release/preflight.py 静态钉住该契约）。
        if (releaseSigningInputs != null) {
            create("release") {
                storeFile = File(releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_PATH"))
                storePassword = releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_STORE_PASSWORD")
                keyAlias = releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_KEY_ALIAS")
                keyPassword = releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_KEY_PASSWORD")
                // M14-175：签名方案与发布门禁钉死一致（verify_artifact 要求
                // v2+v3；操作员实测 AGP 默认产物 v3=false，无法过门禁）。
                // v1 关闭——minSdk 26 ≥ 24，JAR 签名仅为 API 24 以下兼容存在；
                // v2/v3 显式开启。v3.1（密钥轮换扩展块：仅当存在轮换谱系才
                // 有内容可签，且 AGP 8.13 无对应 DSL 旋钮）与 v4（ADB 增量
                // 安装用的独立 .idsig，不参与 APK 本体校验）与本仓库单发布
                // 密钥、整包分发契约无关，保持 AGP 默认（不产出）。
                // tools/android_release/preflight.py 静态钉住本三项不可删改。
                enableV1Signing = false
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        debug {
            // debug 允许 loopback/局域网 HTTP（模拟器 10.0.2.2 / LAN 调试 API）。
            // 明文流量放行也只在 debug 变体（src/debug/AndroidManifest.xml），release 保持默认禁止。
            buildConfigField("boolean", "ALLOW_INSECURE_HTTP", "true")
        }
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // M14-171A：无外部签名输入时不设 signingConfig（保持 unsigned 诚实默认）；
            // 输入不完整的情形已在顶层解析处直接失败，走不到这里。
            if (releaseSigningInputs != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
        // JVM 单测里用 suspend 函数断言状态迁移时屏蔽假警告
        freeCompilerArgs += listOf("-opt-in=kotlinx.coroutines.ExperimentalCoroutinesApi")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    lint {
        // 本切片无静态资源文本国际化需求：缺翻译不作为门禁
        disable += "MissingTranslation"
        warningsAsErrors = false
        abortOnError = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.core)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)

    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
}
