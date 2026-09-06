package com.ailearningos.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.ailearningos.app.di.AiosViewModelFactory
import com.ailearningos.app.ui.AiosApp
import com.ailearningos.app.ui.theme.AiosTheme

/** M12-01：单 Activity + Compose Navigation 壳 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val factory = AiosViewModelFactory((application as AiosApplication).container)
        setContent {
            AiosTheme {
                AiosApp(viewModelFactory = factory)
            }
        }
    }
}
