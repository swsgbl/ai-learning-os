package com.ailearningos.app

import android.app.Application
import com.ailearningos.app.di.AppContainer

class AiosApplication : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
