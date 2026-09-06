// M12-01 Android App Shell：根构建脚本。
// 插件版本集中声明（apply false），具体版本固定在 gradle/libs.versions.toml。
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}
