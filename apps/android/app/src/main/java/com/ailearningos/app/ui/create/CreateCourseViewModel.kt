package com.ailearningos.app.ui.create

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.ui.common.ContentState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * M15-017 定制课程（App 端闭环）：说出学习目标 → 课程方案 → 私有练习卷。
 *
 * 422（目标无概念匹配）如实透出服务端给出的「当前支持主题」——引导
 * 用户换可匹配的目标，不虚构空课程。
 */
data class CreateCourseUiState(
    val goal: String = "",
    val generating: Boolean = false,
    val draft: ContentState<CoursePlanView> = ContentState.Empty,
    val materializing: Boolean = false,
    val notice: String? = null,
    /** materialize 成功产物（UI 据此引导跳学习页开练） */
    val createdPaper: MaterializedPaper? = null,
)

/** plan 渲染视图：章节大纲 + 每章评估题干（plan 原始 JSON 的子集抽取） */
data class CoursePlanView(
    val draftId: String,
    val goal: String,
    val note: String,
    val chapters: List<ChapterView>,
)

data class ChapterView(
    val chapterNo: Int,
    val title: String,
    val difficulty: Int,
    val assessmentStem: String,
)

data class MaterializedPaper(val id: String, val title: String)

class CreateCourseViewModel(private val system: SystemGateway) : ViewModel() {

    private val _state = MutableStateFlow(CreateCourseUiState())
    val state: StateFlow<CreateCourseUiState> = _state.asStateFlow()

    fun onGoalChange(value: String) {
        _state.update { it.copy(goal = value) }
    }

    fun generate() {
        val goal = _state.value.goal.trim()
        if (goal.isEmpty() || _state.value.generating) return
        viewModelScope.launch {
            _state.update { it.copy(generating = true, notice = null, draft = ContentState.Loading) }
            val result = try {
                ContentState.Success(system.generateCourse(goal).toView())
            } catch (cause: Throwable) {
                val message = (cause as? AppError)?.userMessage ?: cause.message ?: "生成失败"
                ContentState.Error(message)
            }
            _state.update { it.copy(generating = false, draft = result) }
        }
    }

    fun materialize() {
        val draft = _state.value.draft as? ContentState.Success ?: return
        if (_state.value.materializing) return
        viewModelScope.launch {
            _state.update { it.copy(materializing = true, notice = null) }
            try {
                val paper = system.materializeCourse(draft.value.draftId)
                _state.update {
                    it.copy(
                        materializing = false,
                        createdPaper = MaterializedPaper(paper.id, paper.title),
                    )
                }
            } catch (cause: Throwable) {
                val message = (cause as? AppError)?.userMessage ?: cause.message ?: "生成试卷失败"
                _state.update { it.copy(materializing = false, notice = message) }
            }
        }
    }

    fun consumeCreatedPaper() {
        _state.update { it.copy(createdPaper = null) }
    }
}

private fun com.ailearningos.app.data.remote.GenerationDraftResponse.toView(): CoursePlanView {
    val chapters = mutableListOf<ChapterView>()
    val outline = plan["outline"]?.let { it as? kotlinx.serialization.json.JsonArray }
    val assessments = plan["assessments"]?.let { it as? kotlinx.serialization.json.JsonArray }
    outline?.forEachIndexed { index, element ->
        val obj = element as? kotlinx.serialization.json.JsonObject ?: return@forEachIndexed
        val assessment = (assessments?.getOrNull(index) as? kotlinx.serialization.json.JsonObject)
        chapters.add(
            ChapterView(
                chapterNo = (obj["chapter_no"] as? kotlinx.serialization.json.JsonPrimitive)?.content?.toIntOrNull() ?: (index + 1),
                title = (obj["title"] as? kotlinx.serialization.json.JsonPrimitive)?.content ?: "",
                difficulty = (obj["difficulty"] as? kotlinx.serialization.json.JsonPrimitive)?.content?.toIntOrNull() ?: 3,
                assessmentStem = (assessment?.get("stem") as? kotlinx.serialization.json.JsonPrimitive)?.content ?: "",
            )
        )
    }
    return CoursePlanView(draftId = id, goal = goal, note = generation_note, chapters = chapters)
}
