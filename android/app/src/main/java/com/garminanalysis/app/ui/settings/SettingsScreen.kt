package com.garminanalysis.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.garminanalysis.app.AppViewModel

@Composable
fun SettingsScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val user = vm.activeUser
    var displayName by remember(user) { mutableStateOf(user?.optString("display_name").orEmpty().ifBlank { "我的配置" }) }
    var apiKey by remember { mutableStateOf("") }
    var region by remember(user) { mutableStateOf(user?.optString("region", "global") ?: "global") }
    var sex by remember(user) { mutableStateOf(user?.optString("sex", "unknown") ?: "unknown") }
    var age by remember(user) {
        mutableStateOf(if (user == null || user.isNull("age")) "" else user.opt("age").toString())
    }
    var hrMax by remember(user) {
        mutableStateOf(if (user == null || user.isNull("hr_max")) "" else user.opt("hr_max").toString())
    }
    var deepseek by remember { mutableStateOf("") }
    var interval by remember(vm.syncPolicy) {
        mutableStateOf(vm.syncPolicy.optInt("min_interval_minutes", 20).toString())
    }
    var dailyCap by remember(vm.syncPolicy) {
        mutableStateOf(vm.syncPolicy.optInt("daily_cap", 12).toString())
    }

    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("设置", style = MaterialTheme.typography.headlineMedium)
        Text("Personal API Key 与 DeepSeek 密钥只保存在本机。", style = MaterialTheme.typography.bodyMedium)

        OutlinedTextField(displayName, { displayName = it }, label = { Text("配置名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            apiKey,
            { apiKey = it },
            label = { Text(if (user?.optBoolean("has_personal_api_key") == true) "Personal API Key（留空则不改；重新粘贴可覆盖）" else "Garmin Personal API Key") },
            modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            singleLine = true,
        )
        Text("地区（须与创建 Key 的 Connect 站点一致）")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = region == "global", onClick = { region = "global" }, label = { Text("国际区") })
            FilterChip(selected = region == "cn", onClick = { region = "cn" }, label = { Text("中国区") })
        }
        Text("性别（可选）")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = sex == "unknown", onClick = { sex = "unknown" }, label = { Text("未填") })
            FilterChip(selected = sex == "male", onClick = { sex = "male" }, label = { Text("男") })
            FilterChip(selected = sex == "female", onClick = { sex = "female" }, label = { Text("女") })
        }
        OutlinedTextField(age, { age = it }, label = { Text("年龄（可选）") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(hrMax, { hrMax = it }, label = { Text("最大心率（可选）") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        Button(
            onClick = {
                vm.saveUser(
                    personalApiKey = apiKey,
                    region = region,
                    displayName = displayName,
                    sex = sex,
                    age = age,
                    hrMax = hrMax,
                )
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("保存配置并设为当前")
        }
        Text(
            "提示：更新已有配置时请重新粘贴 Personal API Key（本机不回显明文）。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Text("DeepSeek", style = MaterialTheme.typography.titleLarge)
        Text("状态：${when (vm.deepseekSource) { "file" -> "已保存在本机"; "env" -> "来自环境变量"; else -> "未配置" }}")
        OutlinedTextField(
            deepseek,
            { deepseek = it },
            label = { Text("DeepSeek API Key") },
            modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            singleLine = true,
        )
        OutlinedTextField(interval, { interval = it }, label = { Text("同步最短间隔（分钟）") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(dailyCap, { dailyCap = it }, label = { Text("每日同步上限") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        Button(
            onClick = { vm.saveDeepseek(deepseek, interval, dailyCap) },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("保存 DeepSeek 与同步限制")
        }
    }
}
