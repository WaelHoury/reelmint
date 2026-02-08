<script setup>
import { ref, onMounted } from 'vue'
import { currentUser } from '../stores/auth'
import { getMyVideos } from '../api'

const videoCount = ref(0)

onMounted(async () => {
  try {
    const { data } = await getMyVideos(1, 1)
    videoCount.value = data.total
  } catch {
    // ignore
  }
})
</script>

<template>
  <div>
    <h2 style="margin-bottom: 24px">Account</h2>

    <el-card v-if="currentUser" style="max-width: 500px">
      <div style="display: flex; align-items: center; gap: 20px; margin-bottom: 24px">
        <el-avatar :size="72" :src="currentUser.picture_url">
          <el-icon :size="36"><User /></el-icon>
        </el-avatar>
        <div>
          <h3 style="margin: 0">{{ currentUser.name }}</h3>
          <span style="color: #909399">{{ currentUser.email }}</span>
        </div>
      </div>

      <el-descriptions :column="1" border>
        <el-descriptions-item label="Email">{{ currentUser.email }}</el-descriptions-item>
        <el-descriptions-item label="Total Videos">{{ videoCount }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>
