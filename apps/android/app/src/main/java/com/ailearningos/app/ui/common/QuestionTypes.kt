package com.ailearningos.app.ui.common

/**
 * 题型显示名（M12-02）：与 Web 端 TYPE_LABELS 对齐；
 * 未知题型如实显示「作答」，不猜测语义。
 */
fun questionTypeLabel(type: String): String = when (type) {
    "mcq" -> "选择"
    "multiple_select" -> "多选"
    "tf", "true_false" -> "判断"
    "short", "short_answer" -> "简答"
    "essay" -> "写作"
    "numeric" -> "数值"
    "math" -> "数学"
    "coding" -> "编程"
    "fill_blank" -> "填空"
    else -> "作答"
}

/** 试卷难度显示名；未知值保留原词 */
fun difficultyLabel(difficulty: String): String = when (difficulty) {
    "basic" -> "基础"
    "core" -> "核心"
    "advanced" -> "进阶"
    else -> difficulty
}
