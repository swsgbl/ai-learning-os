package com.ailearningos.app.ui.login

import androidx.compose.material3.Checkbox
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
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
import androidx.compose.material3.FilterChip
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

/** 登录页：用户名 + 密码 → POST /api/v1/auth/login（勾选记住 = Keystore 加密保存；成功后清空表单态） */
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
        // M15-002: 登录/注册双模式（新用户第一步可直接注册）
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = state.mode == LoginMode.LOGIN,
                onClick = { loginViewModel.onModeChange(LoginMode.LOGIN) },
                label = { Text("登录") },
                enabled = !state.submitting,
            )
            FilterChip(
                selected = state.mode == LoginMode.REGISTER,
                onClick = { loginViewModel.onModeChange(LoginMode.REGISTER) },
                label = { Text("注册") },
                enabled = !state.submitting,
            )
        }

        Text(
            if (state.mode == LoginMode.LOGIN) "登录" else "注册",
            style = MaterialTheme.typography.titleLarge,
        )
        Text(
            if (state.mode == LoginMode.LOGIN) {
                "登录后可以使用与账号绑定的学习数据。"
            } else {
                "第一次使用？创建账号后即可定制课程并开始练习。"
            },
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

        // M15-007 记住密码：Keystore 加密保存，下次自动预填
        if (state.mode == LoginMode.LOGIN) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Start,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Checkbox(
                    checked = state.rememberPassword,
                    onCheckedChange = loginViewModel::onRememberPasswordChange,
                    enabled = !state.submitting,
                )
                Text(
                    "记住密码（本机加密保存）",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

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
                Text(
                    if (state.mode == LoginMode.LOGIN) "登录" else "注册并登录",
                    textAlign = TextAlign.Center,
                )
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
