import { createRouter, createWebHistory } from 'vue-router'

import GovernancePublishView from '@/views/GovernancePublishView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    {
      path: '/governance',
      name: 'governance-publish',
      component: GovernancePublishView,
      props: (route) => ({ batchId: String(route.query.batch ?? 'G71') }),
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
