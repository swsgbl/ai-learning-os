package com.ailearningos.app.data.remote

import kotlinx.serialization.json.Json

/** 全局唯一 Json 配置：生产与测试同源，DTO 解析行为不漂移 */
val AiosJson: Json = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = false
}
