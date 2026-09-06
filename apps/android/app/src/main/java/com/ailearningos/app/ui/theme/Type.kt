package com.ailearningos.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * 标题用衬线（对应 Web 的 Newsreader/Songti 显示字体），正文走系统无衬线；
 * 设备无内置衬线中文字体时由系统回退，不引入额外字体资源。
 */
val AiosTypography = Typography().let { base ->
    base.copy(
        headlineSmall = base.headlineSmall.copy(
            fontFamily = FontFamily.Serif,
            fontWeight = FontWeight.Medium,
        ),
        titleLarge = base.titleLarge.copy(
            fontFamily = FontFamily.Serif,
            fontWeight = FontWeight.Medium,
        ),
        titleMedium = base.titleMedium.copy(fontWeight = FontWeight.SemiBold),
    )
}

/** 首页品牌字（“砚席”） */
val BrandStyle = TextStyle(
    fontFamily = FontFamily.Serif,
    fontWeight = FontWeight.Medium,
    fontSize = 24.sp,
    letterSpacing = 0.sp,
)
