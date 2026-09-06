package com.ailearningos.app.ui.study

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Subtle

/**
 * 学习/考场占位（M12-01）：只说明后续能力，不虚构已接入功能。
 */
@Composable
fun StudyPlaceholderScreen(
    onBack: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    PlaceholderPage(
        title = "学习 / 考场",
        tag = "占位",
        body = "后续版本将在这里接入：试卷抽取与组卷、考场作答与交卷、审阅报告与掌握度。" +
            "当前版本（M12-01 第一切片）只提供应用壳与认证/API 基础，尚未接入考试业务，" +
            "本页面不提供任何作答或提交入口。",
        onBack = onBack,
        onOpenSettings = onOpenSettings,
    )
}

@Composable
internal fun PlaceholderPage(
    title: String,
    tag: String,
    body: String,
    onBack: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Text(
                tag,
                style = MaterialTheme.typography.labelMedium,
                color = Subtle,
                modifier = Modifier.padding(top = 6.dp),
            )
        }

        SectionCard(title = "后续能力") {
            Text(body, style = MaterialTheme.typography.bodyMedium)
        }

        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
                Text("返回首页")
            }
            OutlinedButton(onClick = onOpenSettings, modifier = Modifier.fillMaxWidth()) {
                Text("打开设置")
            }
        }
    }
}
