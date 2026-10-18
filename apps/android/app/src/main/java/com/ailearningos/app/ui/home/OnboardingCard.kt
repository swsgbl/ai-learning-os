package com.ailearningos.app.ui.home

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Subtle

/**
 * M15-007 三步上手引导（移动端）：定制课程（Web）→ 手机开练 → 错因报告。
 *
 * 关闭态经 SharedPreferences 记忆（纯 UI 态，不进业务存储接口）；
 * 已登录后展示（未登录的连接/登录引导由 WelcomeBanner/登录卡承担）。
 */
private const val PREFS = "aios_home_prefs"
private const val KEY_DISMISSED = "onboarding_dismissed"

@Composable
fun OnboardingCard(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    var dismissed by remember {
        mutableStateOf(
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getBoolean(KEY_DISMISSED, false)
        )
    }
    if (!dismissed) {
        SectionCard(title = "三步上手", modifier = modifier) {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("1. 在 Web 端「定制课程」页说出你的学习目标", style = MaterialTheme.typography.bodyMedium)
                Text("2. 生成的课程卷会出现在「学习」页，随时开练", style = MaterialTheme.typography.bodyMedium)
                Text("3. 练完看错因报告，针对性补弱", style = MaterialTheme.typography.bodyMedium)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                ) {
                    TextButton(onClick = {
                        dismissed = true
                        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                            .edit().putBoolean(KEY_DISMISSED, true).apply()
                    }) {
                        Text("知道了")
                    }
                }
                Text(
                    "服务器地址与管理在「设置」页。",
                    style = MaterialTheme.typography.bodySmall,
                    color = Subtle,
                )
            }
        }
    }
}
