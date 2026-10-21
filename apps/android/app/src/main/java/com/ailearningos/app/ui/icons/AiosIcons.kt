package com.ailearningos.app.ui.icons

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/**
 * material-icons-core 之外的三个导航图标（学习/语音/考场）。
 * 路径数据取自 Material Icons（Apache-2.0），语义对应 Web 端的 BookOpen / Headphones / BookOpen。
 */
object AiosIcons {

    val School: ImageVector by lazy { buildIcon("AiosIcons.School", SCHOOL_PATH) }

    val Mic: ImageVector by lazy { buildIcon("AiosIcons.Mic", MIC_PATH) }

    /** M15-025 考场审阅标签（Web 端 BookOpen 同义，menu_book 路径） */
    val BookOpen: ImageVector by lazy { buildIcon("AiosIcons.BookOpen", BOOK_PATH) }

    private fun buildIcon(name: String, pathData: String): ImageVector =
        ImageVector.Builder(
            name = name,
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            addPath(
                pathData = addPathNodes(pathData),
                pathFillType = PathFillType.NonZero,
                fill = SolidColor(Color.Black),
            )
        }.build()

    private const val SCHOOL_PATH =
        "M5,13.18v4L12,21l7,-3.82v-4L12,17l-7,-3.82zM12,3L1,9l11,6,9,-4.91V17h2V9L12,3z"

    private const val MIC_PATH =
        "M12,14c1.66,0 3,-1.34 3,-3V5c0,-1.66 -1.34,-3 -3,-3S9,3.34 9,5v6c0,1.66 1.34,3 3,3z" +
            "m5.3,-3c0,3 -2.54,5.1 -5.3,5.1S6.7,14 6.7,11H5c0,3.41 2.72,6.23 6,6.72V21h2v-3.28" +
            "c3.28,-0.48 6,-3.3 6,-6.72h-1.7z"

    /** Material Icons menu_book（Apache-2.0）：摊开的书，考场审阅 */
    private const val BOOK_PATH =
        "M21,5c-1.11,-0.35 -2.33,-0.5 -3.5,-0.5 -1.95,0 -4.05,0.4 -5.5,1.5 -1.45,-1.1 " +
            "-3.55,-1.5 -5.5,-1.5S2.45,4.9 1,6v14.65c0,0.25 0.25,0.5 0.5,0.5 0.1,0 " +
            "0.15,-0.05 0.25,-0.05C3.1,20.45 5.05,20 6.5,20c1.95,0 4.05,0.4 5.5,1.5 " +
            "1.35,-0.85 3.8,-1.5 5.5,-1.5 1.65,0 3.35,0.3 4.75,1.05 0.1,0.05 0.15,0.05 " +
            "0.25,0.05 0.25,0 0.5,-0.25 0.5,-0.5V6c-0.6,-0.45 -1.25,-0.75 -2,-1zM21,18.5c-1.1," +
            "-0.35 -2.3,-0.5 -3.5,-0.5 -1.7,0 -4.15,0.65 -5.5,1.5V8c1.35,-0.85 3.8,-1.5 " +
            "5.5,-1.5 1.2,0 2.4,0.15 3.5,0.5V18.5z"
}
