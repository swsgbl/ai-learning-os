package com.ailearningos.app.ui.common

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** 统一的 loading/empty/error/success 呈现（错误文案已在上层脱敏） */
@Composable
fun <T> ContentStateView(
    state: ContentState<T>,
    modifier: Modifier = Modifier,
    loadingText: String = "加载中…",
    emptyText: String = "暂无数据",
    retryText: String = "重试",
    onRetry: (() -> Unit)? = null,
    success: @Composable (T) -> Unit,
) {
    when (state) {
        is ContentState.Loading -> LoadingView(loadingText, modifier)
        is ContentState.Empty -> EmptyView(emptyText, modifier, onRetry, retryText)
        is ContentState.Error -> ErrorView(state.message, modifier, onRetry, retryText)
        is ContentState.Success -> success(state.value)
    }
}

@Composable
fun LoadingView(text: String = "加载中…", modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth().padding(vertical = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CircularProgressIndicator(modifier = Modifier.size(22.dp), strokeWidth = 2.dp)
        Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun EmptyView(
    text: String,
    modifier: Modifier = Modifier,
    action: (() -> Unit)? = null,
    actionLabel: String = "重试",
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(vertical = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (action != null) {
            Button(onClick = action) { Text(actionLabel) }
        }
    }
}

@Composable
fun ErrorView(
    message: String,
    modifier: Modifier = Modifier,
    onRetry: (() -> Unit)? = null,
    retryLabel: String = "重试",
) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "出错",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            Text(
                message,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurface,
            )
            if (onRetry != null) {
                Spacer(Modifier.height(2.dp))
                Button(onClick = onRetry) { Text(retryLabel) }
            }
        }
    }
}
