package com.ailearningos.app.ui.search

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.model.SearchOutcome
import com.ailearningos.app.data.model.SearchPlan
import com.ailearningos.app.data.model.SearchProviderEntry
import com.ailearningos.app.data.model.SearchRecord
import com.ailearningos.app.data.model.SearchResultItem
import com.ailearningos.app.ui.common.EmptyView
import com.ailearningos.app.ui.common.ErrorView
import com.ailearningos.app.ui.common.LoadingView
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.components.StatusRow
import com.ailearningos.app.ui.theme.Subtle

/**
 * 搜索页（M12-04）：注册表概览 → 输入查询 → 预览计划 / 执行搜索 → 结果与回查。
 *
 * 呈现边界：可用性/禁用原因、槽位、计划、结果排序理由、skipped 原因全部
 * 原样来自服务端；零结果如实显示空态；错误不静默、不虚报成功。
 */
@Composable
fun SearchScreen(viewModel: SearchViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // 委托属性不能 smart cast：先取局部 val 再进 LazyColumn builder
    val plan = state.plan
    val planError = state.planError
    val outcome = state.outcome
    val searchError = state.searchError

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { Text("搜索", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(top = 12.dp)) }
        item { ProvidersCard(state, onRetry = viewModel::loadProviders) }
        item { QueryCard(state, viewModel) }
        if (plan != null) {
            item { PlanCard(plan) }
        }
        if (planError != null) {
            item { ErrorView(message = planError, onRetry = viewModel::previewPlan) }
        }
        if (outcome != null) {
            item { OutcomeHeaderCard(outcome) }
            if (outcome.results.isEmpty()) {
                item { EmptyView(text = "没有找到结果。可以换个说法，或补充学科、年份等关键词。") }
            } else {
                item { Text("结果", style = MaterialTheme.typography.titleMedium) }
                items(outcome.results.size) { index ->
                    ResultCard(outcome.results[index])
                }
            }
            if (outcome.skipped.isNotEmpty()) {
                item { SkippedCard(outcome.skipped) }
            }
        }
        if (searchError != null) {
            item { ErrorView(message = searchError, onRetry = viewModel::search) }
        }
        item { RecordCard(state, viewModel) }
        item { SpacerBottom() }
    }
}

// ---------- 搜索源注册表 ----------

@Composable
private fun ProvidersCard(state: SearchUiState, onRetry: () -> Unit) {
    SectionCard(title = "搜索源") {
        when {
            state.providersLoading -> LoadingView("正在获取搜索源…")
            state.providersError != null -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "搜索源状态暂不可用，无法确认哪些源可用。",
                    style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedButton(onClick = onRetry) { Text("重试") }
            }
            else -> Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                state.providers.forEach { entry -> ProviderRow(entry) }
                Text(
                    "不可用的源会被跳过并说明原因；是否访问云端由服务端配置决定。",
                    style = MaterialTheme.typography.bodySmall,
                    color = Subtle,
                )
            }
        }
    }
}

@Composable
private fun ProviderRow(entry: SearchProviderEntry) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "${entry.name}（${providerKindLabel(entry.kind)}）",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
            Text(
                if (entry.enabled) "可用" else "不可用",
                style = MaterialTheme.typography.labelMedium,
                color = if (entry.enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            )
        }
        if (!entry.enabled && !entry.unavailableReason.isNullOrEmpty()) {
            Text(
                entry.unavailableReason,
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
        }
    }
}

// ---------- 查询输入 ----------

@Composable
private fun QueryCard(state: SearchUiState, viewModel: SearchViewModel) {
    SectionCard(title = "查询") {
        OutlinedTextField(
            value = state.queryInput,
            onValueChange = viewModel::setQueryInput,
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("如：2024 清华大学 高等数学 选择题") },
            singleLine = true,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            FilledTonalButton(
                onClick = viewModel::previewPlan,
                enabled = !state.planning && !state.searching,
                modifier = Modifier.weight(1f),
            ) {
                if (state.planning) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 6.dp).size(16.dp), strokeWidth = 2.dp)
                }
                Text("预览计划")
            }
            Button(
                onClick = viewModel::search,
                enabled = !state.searching && !state.planning,
                modifier = Modifier.weight(1f),
            ) {
                if (state.searching) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 6.dp).size(16.dp), strokeWidth = 2.dp)
                }
                Text("搜索")
            }
        }
    }
}

// ---------- 计划预览 ----------

@Composable
private fun PlanCard(plan: SearchPlan) {
    SectionCard(title = "查询计划（预览，未执行）") {
        val recognized = slotLabels(plan.slots)
        if (recognized.isEmpty()) {
            Text(
                "未识别出结构化信息，将按原词「${plan.query}」搜索。",
                style = MaterialTheme.typography.bodyMedium,
            )
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("识别到的信息", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                recognized.forEach { (label, value) ->
                    StatusRow(label = label, value = value)
                }
            }
        }
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            plan.items.forEach { item ->
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(item.provider, style = MaterialTheme.typography.bodyMedium)
                        Text(
                            if (item.enabled) "将搜索「${item.query}」" else "不会执行",
                            style = MaterialTheme.typography.labelMedium,
                            color = if (item.enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                        )
                    }
                    if (!item.enabled && !item.unavailableReason.isNullOrEmpty()) {
                        Text(item.unavailableReason, style = MaterialTheme.typography.bodySmall, color = Subtle)
                    }
                }
            }
        }
    }
}

/** 六槽位 → 已识别项（未识别不显示，不虚报） */
private fun slotLabels(slots: com.ailearningos.app.data.model.SearchPlanSlots): List<Pair<String, String>> =
    buildList {
        slots.subject?.let { add("学科" to it) }
        slots.school?.let { add("学校" to it) }
        slots.year?.let { add("年份" to it) }
        slots.course?.let { add("课程" to it) }
        slots.questionType?.let { add("题型" to it) }
        slots.publicity?.let { add("公开范围" to it) }
    }

// ---------- 执行结果 ----------

@Composable
private fun OutcomeHeaderCard(outcome: SearchOutcome) {
    SectionCard(title = "搜索结果概要") {
        StatusRow(label = "查询编号", value = "#${outcome.queryId}")
        StatusRow(label = "结果数", value = "${outcome.resultCount} 条")
        StatusRow(label = "耗时", value = "${outcome.durationMs} ms")
        StatusRow(label = "请求的源", value = outcome.providersRequested.joinToString("、"))
    }
}

@Composable
private fun ResultCard(item: SearchResultItem) {
    SectionCard(title = item.title) {
        Text(item.snippet, style = MaterialTheme.typography.bodyMedium)
        Text(
            buildString {
                append(item.source)
                if (item.provider != item.source) append("·${item.provider}")
                item.authority?.let { append("·${authorityLabel(it)}") }
            },
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )
        Text(
            "排序理由：${item.rankReason}",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )
    }
}

@Composable
private fun SkippedCard(skipped: List<com.ailearningos.app.data.model.SearchSkipped>) {
    SectionCard(title = "被跳过的源") {
        skipped.forEach { entry ->
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(entry.provider, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                Text("原因：${entry.reason}", style = MaterialTheme.typography.bodySmall, color = Subtle)
            }
        }
    }
}

// ---------- query_id 回查 ----------

@Composable
private fun RecordCard(state: SearchUiState, viewModel: SearchViewModel) {
    SectionCard(title = "按编号回查") {
        OutlinedTextField(
            value = state.recordInput,
            onValueChange = viewModel::setRecordInput,
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("输入查询编号，如 42") },
            singleLine = true,
        )
        Button(
            onClick = viewModel::lookupRecord,
            enabled = !state.recordLoading,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (state.recordLoading) {
                CircularProgressIndicator(modifier = Modifier.padding(end = 6.dp).size(16.dp), strokeWidth = 2.dp)
            }
            Text("回查记录")
        }
        if (state.record != null) {
            RecordBody(state.record)
        }
        if (state.recordError != null) {
            Text(
                state.recordError,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun RecordBody(record: SearchRecord) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        StatusRow(label = "原查询词", value = record.query)
        StatusRow(label = "结果数", value = "${record.resultCount} 条")
        StatusRow(label = "耗时", value = "${record.durationMs} ms")
        StatusRow(label = "请求的源", value = record.providersRequested.joinToString("、"))
        StatusRow(label = "记录时间", value = record.createdAt)
        if (record.skipped.isNotEmpty()) {
            Text("被跳过的源", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            record.skipped.forEach { entry ->
                Text("${entry.provider}：${entry.reason}", style = MaterialTheme.typography.bodySmall, color = Subtle)
            }
        }
        if (record.results.isNotEmpty()) {
            Text("结果", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            record.results.forEach { item ->
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(item.title, style = MaterialTheme.typography.bodyMedium)
                    Text(item.snippet, style = MaterialTheme.typography.bodySmall)
                    Text(
                        "${item.provider}·排序理由：${item.rankReason}",
                        style = MaterialTheme.typography.bodySmall,
                        color = Subtle,
                    )
                }
            }
        } else {
            Text("该次搜索没有结果。", style = MaterialTheme.typography.bodySmall, color = Subtle)
        }
    }
}

// ---------- 辅助 ----------

private fun providerKindLabel(kind: String): String = when (kind) {
    "local-corpus" -> "本地语料"
    "web" -> "网页"
    else -> kind
}

private fun authorityLabel(authority: String): String = when (authority) {
    "official" -> "官方来源"
    "community" -> "社区来源"
    else -> authority
}

@Composable
private fun SpacerBottom() {
    Spacer(modifier = Modifier.padding(bottom = 16.dp))
}
