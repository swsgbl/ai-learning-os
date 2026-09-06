package com.ailearningos.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

/**
 * M12-01 只交付浅色方案：Web 端同款 porcelain/ink/deep-teal。
 * 深色主题是后续切片（本切片不虚构已适配）。
 */
private val LightColors = lightColorScheme(
    primary = Accent,
    onPrimary = AccentFg,
    primaryContainer = AccentSoft,
    onPrimaryContainer = AccentStrong,
    secondary = InkSoft,
    onSecondary = AccentFg,
    secondaryContainer = Surface2,
    onSecondaryContainer = Ink,
    tertiary = Warn,
    onTertiary = Paper,
    tertiaryContainer = WarnSoft,
    onTertiaryContainer = Warn,
    error = Bad,
    onError = Paper,
    errorContainer = BadSoft,
    onErrorContainer = Bad,
    background = Bg,
    onBackground = Ink,
    surface = Paper,
    onSurface = Ink,
    surfaceVariant = Surface2,
    onSurfaceVariant = Muted,
    outline = BorderStrong,
    outlineVariant = Border,
    surfaceContainerLowest = Paper,
    surfaceContainerLow = Paper,
    surfaceContainer = Surface2,
    surfaceContainerHigh = Surface2,
    surfaceContainerHighest = Surface3,
    inverseSurface = Ink,
    inverseOnSurface = Bg,
)

@Composable
fun AiosTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = LightColors,
        typography = AiosTypography,
        content = content,
    )
}
