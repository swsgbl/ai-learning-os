package com.ailearningos.app.ui.governance

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.components.StatusRow
import com.ailearningos.app.ui.session.SessionState
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.theme.Subtle

/**
 * 治理 / 发布页（M12-05）：发布版本只读面。
 *
 * 版本四项（版本 / Git / alembic current / head）原词呈现，
 * 客户端不替服务端判断迁移漂移。
 */
@Composable
fun GovernanceScreen(
    viewModel: GovernanceViewModel,
    sessionViewModel: SessionViewModel,
    onBack: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val sessionState by sessionViewModel.state.collectAsStateWithLifecycle()

    // 委托属性不能 smart cast：先取局部 val
    val version = state.version
    val versionError = state.versionError
    val snapshot = state.snapshot
    val snapshotError = state.snapshotError
    val auditEntries = state.auditEntries
    val auditError = state.auditError

    val currentSessionState = sessionState
    val accessText = when (currentSessionState) {
        SessionState.AuthDisabled -> "本地模式"
        is SessionState.Authenticated -> if (currentSessionState.user.isAdmin) "管理员" else "当前账号无治理权限"
        else -> "当前账号无治理权限"
    }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onBack) { Text("返回") }
            TextButton(onClick = viewModel::refresh) { Text("刷新") }
        }

        Text("治理 / 发布", style = MaterialTheme.typography.headlineSmall)

        Text(accessText, style = MaterialTheme.typography.bodyMedium, color = Subtle)

        SectionCard(title = "发布版本") {
            when {
                state.versionLoading -> Text("读取中…")
                versionError != null -> Text(versionError, color = MaterialTheme.colorScheme.error)
                version != null -> {
                    StatusRow(label = "版本", value = version.version)
                    StatusRow(label = "Git", value = version.gitCommit)
                    StatusRow(label = "Alembic 当前", value = version.alembicCurrent)
                    StatusRow(label = "Alembic 目标", value = version.alembicHead)
                }
                else -> Text("暂无数据")
            }
        }

        SectionCard(title = "运行快照") {
            when {
                state.snapshotLoading -> Text("读取中…")
                snapshotError != null -> Text(snapshotError, color = MaterialTheme.colorScheme.error)
                snapshot != null -> {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        // 状态键 / 布尔 / 时间原词呈现，不翻译、不推断
                        Text("generatedAt：${snapshot.generatedAt}", style = MaterialTheme.typography.bodyMedium)
                        Text("databaseBackend：${snapshot.databaseBackend}", style = MaterialTheme.typography.bodyMedium)
                        Text("workerRunning：${snapshot.workerRunning.toString()}", style = MaterialTheme.typography.bodyMedium)
                        Text("papersTotal：${snapshot.papersTotal}", style = MaterialTheme.typography.bodyMedium)
                        Text("searchQueriesTotal：${snapshot.searchQueriesTotal}", style = MaterialTheme.typography.bodyMedium)
                        Text("auditEntriesTotal：${snapshot.auditEntriesTotal}", style = MaterialTheme.typography.bodyMedium)
                        Text("courseImport：${snapshot.pendingReviewDrafts.courseImport}", style = MaterialTheme.typography.bodyMedium)
                        Text("courseGeneration：${snapshot.pendingReviewDrafts.courseGeneration}", style = MaterialTheme.typography.bodyMedium)
                        Text("paperQuestion：${snapshot.pendingReviewDrafts.paperQuestion}", style = MaterialTheme.typography.bodyMedium)
                        Text("variantQuestion：${snapshot.pendingReviewDrafts.variantQuestion}", style = MaterialTheme.typography.bodyMedium)
                        snapshot.usersByRole.forEach { (key, value) ->
                            Text("$key：$value", style = MaterialTheme.typography.bodyMedium)
                        }
                        snapshot.resourcesByParseStatus.forEach { (key, value) ->
                            Text("$key：$value", style = MaterialTheme.typography.bodyMedium)
                        }
                        snapshot.parseJobsByStatus.forEach { (key, value) ->
                            Text("$key：$value", style = MaterialTheme.typography.bodyMedium)
                        }
                        snapshot.voiceSessionsByStatus.forEach { (key, value) ->
                            Text("$key：$value", style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
                else -> Text("暂无数据")
            }
        }

        SectionCard(title = "审计留痕") {
            when {
                state.auditLoading -> Text("读取中…")
                auditError != null -> Text(auditError, color = MaterialTheme.colorScheme.error)
                auditEntries.isEmpty() -> Text("暂无审计记录")
                else -> auditEntries.forEach { entry ->
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        // 字段原词呈现，不含 before/after 载荷；actor 为空（系统动作）显示 null
                        Text("id：${entry.id}", style = MaterialTheme.typography.bodyMedium)
                        Text("actor_username：${entry.actorUsername}", style = MaterialTheme.typography.bodyMedium)
                        Text("action：${entry.action}", style = MaterialTheme.typography.bodyMedium)
                        Text("target_type：${entry.targetType}", style = MaterialTheme.typography.bodyMedium)
                        Text("target_id：${entry.targetId}", style = MaterialTheme.typography.bodyMedium)
                        Text("request_id：${entry.requestId}", style = MaterialTheme.typography.bodyMedium)
                        Text("created_at：${entry.createdAt}", style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        }
    }
}
