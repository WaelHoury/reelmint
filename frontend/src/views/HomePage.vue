<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="home-page">
    <div class="home-header">
      <h1 class="home-title">What do you want to <span class="ce-gradient-text">create</span>?</h1>
      <p class="home-subtitle">Choose a format to get started</p>
    </div>

    <div class="cards-grid">
      <!-- Create Shorts -->
      <div class="glass-card glass-card-hover creation-card" @click="router.push('/create/shorts')">
        <div class="card-icon">
          <el-icon :size="40"><Iphone /></el-icon>
        </div>
        <h2 class="card-title">Create Shorts</h2>
        <p class="card-description">
          Create engaging short-form vertical videos perfect for social media
        </p>
        <div class="card-tags">
          <span class="ce-tag">Portrait</span>
          <span class="ce-tag">Square</span>
          <span class="ce-tag">15s-3 min</span>
        </div>
        <button class="btn-primary-glow card-btn">
          <el-icon><Plus /></el-icon>
          Get Started
        </button>
      </div>

      <!-- Create Long Form -->
      <div class="glass-card glass-card-hover creation-card" @click="router.push('/create/longform')">
        <div class="card-icon">
          <el-icon :size="40"><Film /></el-icon>
        </div>
        <h2 class="card-title">Create Long Form</h2>
        <p class="card-description">
          Create in-depth long-form content for YouTube, presentations, and more
        </p>
        <div class="card-tags">
          <span class="ce-tag">Landscape</span>
          <span class="ce-tag">Portrait</span>
          <span class="ce-tag">1-10 min</span>
        </div>
        <button class="btn-primary-glow card-btn">
          <el-icon><Plus /></el-icon>
          Get Started
        </button>
      </div>

      <!-- Autopilot -->
      <div class="glass-card glass-card-hover autopilot-card" @click="router.push('/autopilot/new')">
        <div class="card-icon small">
          <el-icon :size="28"><Timer /></el-icon>
        </div>
        <div class="autopilot-text">
          <h2 class="card-title">Autopilot – queue many videos</h2>
          <p class="autopilot-description">
            Paste a list of topics or scripts. Videos are made one at a time and can be published to YouTube on a schedule.
          </p>
        </div>
        <button class="btn-secondary autopilot-btn">
          New batch
          <el-icon><ArrowRight /></el-icon>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 100px);
  padding: 40px 20px;
}

.home-header {
  text-align: center;
  margin-bottom: 48px;
}

.home-title {
  font-size: 36px;
  font-weight: 800;
  letter-spacing: -0.5px;
  color: var(--ce-text-primary);
  margin-bottom: 12px;
}

.home-subtitle {
  font-size: 16px;
  color: var(--ce-text-muted);
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 28px;
  max-width: 780px;
  width: 100%;
}

.creation-card {
  padding: 36px;
  text-align: center;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.card-icon {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: rgba(62, 230, 168, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ce-accent-light);
  margin-bottom: 20px;
  transition: background var(--ce-transition-normal);
}

.creation-card:hover .card-icon {
  background: rgba(62, 230, 168, 0.18);
}

.card-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin-bottom: 10px;
}

.card-description {
  font-size: 14px;
  color: var(--ce-text-muted);
  line-height: 1.5;
  margin-bottom: 18px;
  max-width: 280px;
}

.card-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
  margin-bottom: 24px;
}

.card-btn {
  width: 100%;
  padding: 14px 24px;
}

.autopilot-card {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 24px 28px;
  cursor: pointer;
}

.card-icon.small {
  width: 56px;
  height: 56px;
  margin-bottom: 0;
  flex-shrink: 0;
}

.autopilot-card:hover .card-icon {
  background: rgba(62, 230, 168, 0.18);
}

.autopilot-text {
  flex: 1;
  min-width: 0;
}

.autopilot-text .card-title {
  font-size: 17px;
  margin-bottom: 4px;
}

.autopilot-description {
  font-size: 13px;
  color: var(--ce-text-muted);
  line-height: 1.5;
}

.autopilot-btn {
  flex-shrink: 0;
  padding: 10px 18px;
  font-size: 13px;
}

@media (max-width: 640px) {
  .autopilot-card {
    flex-direction: column;
    text-align: center;
  }

  .autopilot-btn {
    width: 100%;
  }

  .cards-grid {
    grid-template-columns: 1fr;
    max-width: 380px;
  }

  .home-title {
    font-size: 28px;
  }
}
</style>
