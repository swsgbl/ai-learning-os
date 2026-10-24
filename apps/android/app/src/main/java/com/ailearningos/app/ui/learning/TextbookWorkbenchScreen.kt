package com.ailearningos.app.ui.learning

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Badge
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * M20-001 教材工作台（App/Web 同步）：目录搜索 → 导入 → 我的书架
 * （分态操作：解析/OCR/概念/一键开冲刺）。服务端权威——本屏只投影。
 */
@Composable
fun TextbookWorkbenchScreen(viewModel: TextbookViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { viewModel.refreshShelf() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("教材工作台", style = MaterialTheme.typography.titleLarge)
        Text(
            "搜教材目录（ChinaTextbook）→ 导入我的教材 → 解析 → 一键开学习冲刺",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        state.error?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
        state.notice?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
        }

        // ---------- 目录搜索 ----------
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = state.query,
                onValueChange = viewModel::setQuery,
                placeholder = { Text("搜书名，如：高等数学") },
                modifier = Modifier.weight(1f),
                singleLine = true,
            )
            Button(
                onClick = viewModel::search,
                enabled = !state.searching && state.query.isNotBlank(),
            ) { Text(if (state.searching) "…" else "搜索") }
        }
        state.searchResults?.let { results ->
            Text("目录命中 ${results.size} 条", style = MaterialTheme.typography.labelMedium)
            LazyColumn(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(results, key = { it.id }) { resource ->
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(resource.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium, maxLines = 2)
                            Text(
                                listOf(resource.educationStage, resource.subject, resource.grade)
                                    .filter { it.isNotBlank() }.joinToString(" · ")
                                    .ifEmpty { "未分类" },
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        OutlinedButton(
                            onClick = { viewModel.importResource(resource.id) },
                            enabled = state.importingId == null,
                        ) { Text(if (state.importingId == resource.id) "导入中…" else "导入") }
                    }
                }
            }
        }

        // ---------- 我的书架 ----------
        Text("我的教材", style = MaterialTheme.typography.titleMedium)
        val shelf = state.imports
        when {
            state.loadingShelf -> CircularProgressIndicator(Modifier.padding(16.dp))
            shelf == null || shelf.isEmpty() -> Text(
                "还没有导入的教材——搜到书后点「导入」（你需确认对内容有合法使用权）",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            else -> LazyColumn(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(shelf, key = { it.id }) { job ->
                    ShelfItem(job = job, busy = state.busyJobId == job.id, viewModel = viewModel)
                }
            }
        }
    }
}

@Composable
private fun ShelfItem(
    job: com.ailearningos.app.data.remote.ImportJobDto,
    busy: Boolean,
    viewModel: TextbookViewModel,
) {
    val parsed = job.parseStatus == "parsed"
    val needsOcr = job.parseStatus == "needs_ocr"
    Column(
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            job.title.ifBlank { job.resourceId },
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            maxLines = 2,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Badge { Text(job.status) }
            if (job.parseStatus.isNotBlank()) Badge { Text(parseLabel(job.parseStatus)) }
            Text(
                "%.1f MB".format(job.sizeBytes / 1048576.0),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (job.conceptCounts.isNotBlank()) {
            Text("概念（${job.conceptCounts}）", style = MaterialTheme.typography.labelSmall)
        }
        if (job.status != "ready" && job.error.isNotBlank()) {
            Text(job.error.take(100), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (job.status == "ready" && job.parseStatus.isBlank()) {
                OutlinedButton(enabled = !busy, onClick = { viewModel.parseJob(job.id, ocr = false) }) { Text("解析") }
            }
            if (needsOcr) {
                OutlinedButton(enabled = !busy, onClick = { viewModel.parseJob(job.id, ocr = true) }) { Text("OCR 解析") }
            }
            if (parsed) {
                OutlinedButton(enabled = !busy, onClick = { viewModel.extractConcepts(job.id) }) { Text("提取概念") }
                Button(enabled = !busy, onClick = { viewModel.createSprint(job.id) }) { Text("一键开冲刺") }
            }
        }
        if (busy) CircularProgressIndicator(Modifier.padding(4.dp))
    }
}

private fun parseLabel(status: String): String = when (status) {
    "parsed" -> "已解析"
    "needs_ocr" -> "扫描书"
    "pending" -> "待解析"
    else -> status
}
