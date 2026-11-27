package com.garminanalysis.app.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.garminanalysis.app.AppViewModel
import com.garminanalysis.app.ui.coach.CoachScreen
import com.garminanalysis.app.ui.data.DataScreen
import com.garminanalysis.app.ui.settings.SettingsScreen

@Composable
fun GarminApp(vm: AppViewModel = viewModel()) {
    var tab by mutableIntStateOf(0)
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(vm.statusMessage) {
        val msg = vm.statusMessage ?: return@LaunchedEffect
        snackbar.showSnackbar(msg)
        vm.clearStatus()
    }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = tab == 0,
                    onClick = { tab = 0 },
                    icon = { Icon(Icons.Default.ShowChart, contentDescription = "数据") },
                    label = { Text("数据") },
                )
                NavigationBarItem(
                    selected = tab == 1,
                    onClick = { tab = 1 },
                    icon = { Icon(Icons.Default.Chat, contentDescription = "教练") },
                    label = { Text("教练") },
                )
                NavigationBarItem(
                    selected = tab == 2,
                    onClick = { tab = 2 },
                    icon = { Icon(Icons.Default.Settings, contentDescription = "设置") },
                    label = { Text("设置") },
                )
            }
        },
    ) { padding ->
        when (tab) {
            0 -> DataScreen(vm, Modifier.padding(padding))
            1 -> CoachScreen(vm, Modifier.padding(padding))
            else -> SettingsScreen(vm, Modifier.padding(padding))
        }
    }
}
