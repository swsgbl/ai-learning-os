package com.ailearningos.app.ui.login

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.ui.theme.Subtle

/** 登录页：用户名 + 密码 → POST /api/v1/auth/login（密码不持久化，成功后即清空表单态） */
@Composable
fun LoginScreen(
    loginViewModel: LoginViewModel,
    onSuccess: () -> Unit,
) {
    val state by loginViewModel.state.collectAsStateWithLifecycle()

    LaunchedEffect(state.success) {
        if (state.success) onSuccess()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("登录", style = MaterialTheme.typography.titleLarge)
        Text(
            "登录后可以使用与账号绑定的学习数据。",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        OutlinedTextField(
            value = state.username,
            onValueChange = loginViewModel::onUsernameChange,
            label = { Text("用户名") },
            singleLine = true,
            enabled = !state.submitting,
            modifier = Modifier.fillMaxWidth(),
        )

        OutlinedTextField(
            value = state.password,
            onValueChange = loginViewModel::onPasswordChange,
            label = { Text("密码") },
            singleLine = true,
            enabled = !state.submitting,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )

        state.error?.let { message ->
            Text(
                message,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
        }

        Button(
            onClick = loginViewModel::submit,
            enabled = state.canSubmit,
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            if (state.submitting) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            } else {
                Text("登录", textAlign = TextAlign.Center)
            }
        }

        Text(
            "登录令牌只保存在本机加密存储（Android Keystore）；应用不保存密码，" +
                "也不会把令牌写入日志或统计。",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )
    }
}
