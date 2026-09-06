package com.ailearningos.app.ui.icons

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/**
 * material-icons-core 之外的两个导航图标（学习/语音）。
 * 路径数据取自 Material Icons（Apache-2.0），语义对应 Web 端的 BookOpen / Headphones。
 */
object AiosIcons {

    val School: ImageVector by lazy { buildIcon("AiosIcons.School", SCHOOL_PATH) }

    val Mic: ImageVector by lazy { buildIcon("AiosIcons.Mic", MIC_PATH) }

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
}
