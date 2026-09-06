package com.ailearningos.app.ui.exam

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.ailearningos.app.data.model.ExamQuestion
import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.ui.common.questionTypeLabel
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Accent
import com.ailearningos.app.ui.theme.AccentFg
import com.ailearningos.app.ui.theme.Border
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * 题目卡（M12-02）：按题型稳定录入——
 * - 有选项且非多选（mcq / true_false 等）：点选即保存（append-only 单事件）；
 * - multiple_select：勾选 + 「保存多选」一次提交 {"option_indices":[...]}；
 * - 无选项（short_answer / essay / numeric / 未知题型）：文本 + 保存按钮。
 * 语义统一走 onChoose(questionId, answer) 单一同步链路。
 */
@Composable
internal fun QuestionCard(
    question: ExamQuestion,
    index: Int,
    total: Int,
    answer: String?,
    enabled: Boolean,
    onChoose: (String, String) -> Unit,
) {
    SectionCard(title = "第 ${index + 1} 题 / 共 $total 题") {
        Text(
            questionTypeLabel(question.type),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(question.stem, style = MaterialTheme.typography.bodyLarge)

        when {
            question.isSingleChoice -> SingleChoiceOptions(
                question = question,
                selected = answer,
                enabled = enabled,
                onChoose = onChoose,
            )

            question.isMultipleSelect -> MultipleSelectOptions(
                question = question,
                answer = answer,
                enabled = enabled,
                onChoose = onChoose,
            )

            else -> TextInput(
                question = question,
                answer = answer,
                enabled = enabled,
                onChoose = onChoose,
            )
        }
    }
}

@Composable
private fun SingleChoiceOptions(
    question: ExamQuestion,
    selected: String?,
    enabled: Boolean,
    onChoose: (String, String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        question.options.forEach { option ->
            val active = selected == option.key
            Surface(
                onClick = { if (enabled) onChoose(question.id, option.key) },
                enabled = enabled,
                shape = MaterialTheme.shapes.medium,
                color = if (active) Accent else MaterialTheme.colorScheme.surface,
                contentColor = if (active) AccentFg else MaterialTheme.colorScheme.onSurface,
                border = BorderStroke(1.dp, if (active) Accent else Border),
                modifier = Modifier
                    .fillMaxWidth()
                    .semantics {
                        contentDescription = if (active) {
                            "选项 ${option.key}，已选中"
                        } else {
                            "选项 ${option.key}"
                        }
                    },
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(option.key, style = MaterialTheme.typography.labelLarge)
                    Text(option.text, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

@Composable
private fun MultipleSelectOptions(
    question: ExamQuestion,
    answer: String?,
    enabled: Boolean,
    onChoose: (String, String) -> Unit,
) {
    val serverSelected = remember(answer) { parseOptionIndices(answer) }
    var draft by remember(question.id) { mutableStateOf(serverSelected) }

    // 服务端值变化（恢复/同步对齐）时重置本地草稿
    LaunchedEffect(answer) { draft = serverSelected }

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        question.options.forEachIndexed { index, option ->
            val checked = index in draft
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(
                    checked = checked,
                    onCheckedChange = { include ->
                        draft = if (include) draft + index else draft - index
                    },
                    enabled = enabled,
                )
                Text(
                    "${option.key}. ${option.text}",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(start = 4.dp),
                )
            }
        }
        Button(
            onClick = { onChoose(question.id, encodeOptionIndices(draft)) },
            enabled = enabled && draft.isNotEmpty(),
            modifier = Modifier.fillMaxWidth(),
        ) { Text("保存多选（已选 ${draft.size} 项）") }
        if (draft.isEmpty() && answer != null) {
            Text(
                "当前未选择任何选项",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun TextInput(
    question: ExamQuestion,
    answer: String?,
    enabled: Boolean,
    onChoose: (String, String) -> Unit,
) {
    var draft by remember(question.id) { mutableStateOf(answer.orEmpty()) }

    // 服务端值变化（恢复/同步对齐）时重置本地草稿
    LaunchedEffect(answer) { draft = answer.orEmpty() }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = draft,
            onValueChange = { draft = it },
            enabled = enabled,
            placeholder = { Text("输入答案后保存") },
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = {
                val text = draft.trim()
                if (text.isNotEmpty()) onChoose(question.id, text)
            },
            enabled = enabled && draft.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) { Text("保存答案") }
    }
}

// ---------- 多选答案编码（服务端契约：{"option_indices":[...]}，0 基） ----------

internal fun encodeOptionIndices(indices: Set<Int>): String {
    val sorted = indices.sorted()
    val payload = StringBuilder("{")
    payload.append("\"option_indices\":[")
    sorted.forEachIndexed { index, value ->
        if (index > 0) payload.append(',')
        payload.append(value.toString())
    }
    payload.append("]}")
    return payload.toString()
}

internal fun parseOptionIndices(answer: String?): Set<Int> {
    if (answer.isNullOrBlank() || !answer.startsWith("{")) return emptySet()
    return runCatching {
        AiosJson.parseToJsonElement(answer)
            .jsonObject["option_indices"]
            ?.jsonArray
            ?.map { it.jsonPrimitive.int }
            ?.toSet()
            ?: emptySet()
    }.getOrDefault(emptySet())
}
