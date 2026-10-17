package com.ailearningos.app.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.ailearningos.app.ui.theme.Subtle

/**
 * M15-004 首次使用引导横幅（Android）。
 *
 * 场景：首次打开（从未保存过服务器地址）时在首页顶部渲染，
 * 代替满屏「无法连接 API」的裸奔体验：欢迎语 + 一句话产品价值
 * （定制课程/练习/报告）+「去连接服务器」直达设置。
 *
 * 边界：只做引导跳转，不内联 URL 表单（设置页已有完整校验表单，
 * 零重复）；已保存过地址后永不显示。
 */
@Composable
fun WelcomeBanner(
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "欢迎使用砚席",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                "说出学习目标即可定制专属课程，练完看错因报告。" +
                    "先连接你的砚席服务器开始。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(onClick = onOpenSettings, modifier = Modifier.fillMaxWidth()) {
                Text("连接服务器")
            }
            Text(
                "连上后：在设置页注册/登录账号；课程定制在 Web 端「定制课程」页完成，移动端即可开练。",
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
        }
    }
}
