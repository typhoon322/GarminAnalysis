package com.garminanalysis.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.garminanalysis.app.ui.GarminApp
import com.garminanalysis.app.ui.theme.GarminAnalysisTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            GarminAnalysisTheme {
                GarminApp()
            }
        }
    }
}
