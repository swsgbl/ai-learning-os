package com.ailearningos.app.ui.settings

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import kotlin.math.roundToInt
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * M21 应用内自更新：后台静默下载 + SHA-256 校验 + 应用内安装（免跳浏览器）。
 *
 * 流程：检查到新版本 → startDownload 后台拉 APK（进度回调）→ 校验
 * manifest 同源 SHA-256（fail-closed：无哈希/哈希不符都拒绝安装，绝不
 * 装未验证文件）→ Ready 态提示「已下载，点击安装」→ launchInstall 触发
 * 系统包安装器（覆盖安装 = 应用内"重启更新"体验）。
 *
 * 系统边界（如实）：Android 8+ 首次自装需用户在系统设置给本 App 一次性
 * 「安装未知应用」授权（[ensureInstallPermission] 引导）；系统安装器仍会
 * 显示确认框——这是平台安全模型，无法也不应绕过。
 */
sealed interface ApkUpdateState {
    data object Idle : ApkUpdateState

    /** 下载中：progress 0..100（无 Content-Length 时恒 0，如实展示「下载中」） */
    data class Downloading(val progressPercent: Int) : ApkUpdateState

    data object Verifying : ApkUpdateState

    /** 下载校验完成，等待用户点击安装；versionName 用于「重启更新到 vX」文案 */
    data class Ready(val versionName: String) : ApkUpdateState

    /** 下载/校验失败如实透出；保留跳转下载页兜底 */
    data class Failed(val message: String) : ApkUpdateState
}

/** 下载校验完成的安装文件（VM Ready 态持有，安装时读） */
data class DownloadedApk(val file: File, val versionName: String)

object ApkSelfUpdate {

    internal const val UPDATE_DIR = "updates"

    /**
     * 流式下载 APK 到应用私有缓存并逐块计算 SHA-256。
     * 失败（网络/磁盘/哈希不符）抛 [IOException]——上层如实转 Failed，绝不装。
     */
    suspend fun downloadAndVerify(
        client: OkHttpClient,
        url: String,
        expectedSha256: String,
        versionCode: Int,
        versionName: String,
        cacheDir: File,
        onProgress: (Int) -> Unit,
    ): DownloadedApk = withContext(Dispatchers.IO) {
        val response = client.newCall(Request.Builder().url(url).build()).execute()
        response.use { resp ->
            if (!resp.isSuccessful) throw IOException("下载失败（HTTP ${resp.code}）")
            val body = resp.body ?: throw IOException("下载响应为空")
            val dir = File(cacheDir, UPDATE_DIR).apply { mkdirs() }
            // 版本号入文件名：多版本残留不混淆，Ready 装的就是校验过的那个
            val tmp = File(dir, "update-$versionCode.apk.part")
            val final = File(dir, "update-$versionCode.apk")
            val digest = MessageDigest.getInstance("SHA-256")
            var readTotal = 0L
            val total = body.contentLength()
            tmp.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        digest.update(buf, 0, n)
                        readTotal += n
                        if (total > 0) {
                            onProgress((readTotal * 100.0 / total).roundToInt().coerceIn(0, 100))
                        }
                    }
                }
            }
            if (readTotal == 0L) {
                tmp.delete()
                throw IOException("下载内容为空")
            }
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            if (!actual.equals(expectedSha256.lowercase().trim(), ignoreCase = true)) {
                tmp.delete()
                throw IOException("安装包校验失败（SHA-256 不符），已丢弃")
            }
            if (final.exists()) final.delete()
            if (!tmp.renameTo(final)) throw IOException("安装包落盘失败")
            DownloadedApk(final, versionName)
        }
    }

    /** 是否已具备系统「安装未知应用」授权（Android 8+ 一次性授权） */
    fun canRequestInstall(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }

    /**
     * 引导到系统「安装未知应用」授权页（本 App 条目）。
     * 返回 false = 平台不支持该入口（由调用方如实提示走下载页兜底）。
     */
    fun ensureInstallPermission(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return true
        if (canRequestInstall(context)) return true
        return try {
            val intent = Intent(
                android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${context.packageName}"),
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            true
        } catch (_: Exception) {
            false
        }
    }

    /**
     * 触发系统包安装器安装已校验的 APK（FileProvider content:// 授权读取）。
     * 返回 false = 无法启动安装器（调用方兜底提示跳转下载页）。
     */
    fun launchInstall(context: Context, apk: File): Boolean = try {
        val uri: Uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.updatefileprovider",
            apk,
        )
        val intent = Intent(Intent.ACTION_INSTALL_PACKAGE)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        true
    } catch (_: Exception) {
        false
    }
}
