plugins {
    id("io.gitlab.arturbosch.detekt") version "1.23.8"
}

detekt {
    buildUponDefaultConfig = true
    config.setFrom(rootProject.files("config/detekt/detekt.yml"))
    baseline = file("detekt-baseline.xml")
}

kotlin {
    compilerOptions {
        allWarningsAsErrors.set(true)
    }
}

android {
    lint {
        warningsAsErrors = true
        abortOnError = true
        checkDependencies = true
        baseline = file("lint-baseline.xml")
        lintConfig = rootProject.file("lint.xml")
    }
}
