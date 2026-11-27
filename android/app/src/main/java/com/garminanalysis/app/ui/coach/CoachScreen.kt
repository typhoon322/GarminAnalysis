package com.garminanalysis.app.ui.coach

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.garminanalysis.app.AppViewModel
import org.json.JSONObject

@Composable
fun CoachScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    var input by remember { mutableStateOf("") }
    val messages = remember(vm.chatMessages) {
        (0 until vm.chatMessages.length()).mapNotNull { vm.chatMessages.optJSONObject(it) }
    }
    val listState = rememberLazyListState()
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.lastIndex)
    }
    Column(modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("AI 教练", style = MaterialTheme.typography.headlineMedium)
        Text(
            "基于本机压缩摘要与个人画像时间线提问。密钥不会发给模型正文。",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (vm.coachState.optBoolean("rate_limited")) {
            Text(vm.coachState.optString("message"), color = MaterialTheme.colorScheme.error)
        }
        val timeline = vm.athleteProfile.optJSONArray("timeline")
        if (timeline != null && timeline.length() > 0) {
            Text("个人画像时间线", style = MaterialTheme.typography.titleMedium)
            for (i in (timeline.length() - 1) downTo maxOf(0, timeline.length() - 5)) {
                val item = timeline.optJSONObject(i) ?: continue
                Text(
                    "${item.optString("at")} · ${item.optString("source")} · ${item.optString("note")}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(messages) { msg ->
                ChatBubble(msg)
            }
        }
        OutlinedTextField(
            value = input,
            onValueChange = { input = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("问教练…") },
            minLines = 2,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    vm.sendChat(input)
                    input = ""
                },
                enabled = !vm.chatBusy && input.isNotBlank(),
                modifier = Modifier.weight(1f),
            ) {
                Text(if (vm.chatBusy) "思考中…" else "发送")
            }
            OutlinedButton(onClick = { vm.clearChat() }) {
                Text("清空")
            }
        }
    }
}

@Composable
private fun ChatBubble(msg: JSONObject) {
    val role = msg.optString("role")
    val isUser = role == "user"
    Column(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
    ) {
        Text(
            if (isUser) "我" else "教练",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(msg.optString("content"), style = MaterialTheme.typography.bodyLarge)
    }
}
