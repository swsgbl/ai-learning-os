package com.ailearningos.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.ailearningos.app.ui.session.SessionState
import com.ailearningos.app.ui.theme.Good
import com.ailearningos.app.ui.theme.Subtle
import com.ailearningos.app.ui.theme.Warn

/** 「纸张」卡片：统一标题 + 内容间距 */
@Composable
fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
fun StatusRow(
    label: String,
    value: String,
    valueColor: Color = MaterialTheme.colorScheme.onSurface,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            color = valueColor,
        )
    }
}

/** 当前用户状态卡片（首页与设置共用） */
@Composable
fun SessionCard(
    state: SessionState,
    notice: String?,
    onNoticeDismissed: () -> Unit,
    onLogin: () -> Unit,
    onLogout: () -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    SectionCard(title = "当前用户", modifier = modifier) {
        when (state) {
            is SessionState.Loading -> Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                Text("正在探测登录状态…", style = MaterialTheme.typography.bodyMedium)
            }

            is SessionState.Unavailable -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    state.message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                TextButton(onClick = onRetry) { Text("重试") }
            }

            is SessionState.AuthDisabled -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("本地模式（API 未启用认证）", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "服务端未配置认证密钥，数据仅保存在服务端本机。",
                    style = MaterialTheme.typography.bodySmall,
                    color = Subtle,
                )
            }

            is SessionState.Anonymous -> Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("未登录", style = MaterialTheme.typography.bodyMedium)
                Button(onClick = onLogin) { Text("登录") }
            }

            is SessionState.Authenticated -> Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        state.user.username,
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        roleLabel(state.user.role),
                        style = MaterialTheme.typography.bodySmall,
                        color = Subtle,
                    )
                }
                TextButton(onClick = onLogout) { Text("退出登录") }
            }
        }

        if (notice != null) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    notice,
                    style = MaterialTheme.typography.bodySmall,
                    color = Warn,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = onNoticeDismissed) { Text("知道了") }
            }
        }
    }
}

/** 角色显示名；未知值如实显示，不猜测 */
fun roleLabel(role: String): String = when (role) {
    "admin" -> "管理员"
    "learner" -> "学习者"
    else -> "未知角色"
}

/** 隐私模式显示名；未知值如实显示 */
fun privacyLabel(value: String?): String = when (value) {
    "local" -> "本地"
    "cloud" -> "云端"
    "hybrid" -> "混合"
    else -> "未知"
}

@Composable
fun statusColor(ok: Boolean): Color = if (ok) Good else MaterialTheme.colorScheme.error
