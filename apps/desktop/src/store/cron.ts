import { atom } from 'nanostores'

import type { CronJob } from '@/types/hermes'

// Cron *jobs* (not run sessions) power the sidebar "Cron jobs" section. Listing
// the job — schedule, state, live next-run countdown — makes the job the
// first-class entity; its runs (sessions) resolve under it in the cron detail.
export const $cronJobs = atom<CronJob[]>([])

// Cheap signature compare, mirroring sameCronSignature for cron SESSIONS
// (desktop-controller-utils.ts): the jobs list is re-fetched on a 30s poll AND
// after every completed turn, and the wire always yields a fresh array.
// nanostores only skips notify on reference equality, so an unguarded set
// re-rendered the ENTIRE sidebar every poll with zero visible change. Keep the
// previous reference unless a rendered field actually changed. (The next-run
// countdown ticks off its own local clock; it does not need this churn.)
function sameCronJobs(a: CronJob[], b: CronJob[]): boolean {
  if (a.length !== b.length) {
    return false
  }

  return a.every((job, i) => {
    const other = b[i]

    return (
      other != null &&
      job.id === other.id &&
      job.enabled === other.enabled &&
      job.name === other.name &&
      job.state === other.state &&
      job.next_run_at === other.next_run_at &&
      job.last_run_at === other.last_run_at &&
      job.last_error === other.last_error &&
      job.schedule_display === other.schedule_display &&
      job.schedule?.expr === other.schedule?.expr &&
      job.schedule?.display === other.schedule?.display
    )
  })
}

export const setCronJobs = (jobs: CronJob[]) => {
  if (!sameCronJobs($cronJobs.get(), jobs)) {
    $cronJobs.set(jobs)
  }
}

// In-place edit so the cron overlay's mutations (create/edit/delete/pause/…)
// land in the same atom the sidebar renders — no stale list until the next poll.
export const updateCronJobs = (fn: (jobs: CronJob[]) => CronJob[]) => $cronJobs.set(fn($cronJobs.get()))

// One-shot focus target: clicking "Manage" on a job sets this, then opens the
// cron overlay, which reads it once to select + scroll to that job. Cleared
// after consumption so re-opening cron normally doesn't re-focus a stale job.
export const $cronFocusJobId = atom<null | string>(null)
export const setCronFocusJobId = (id: null | string) => $cronFocusJobId.set(id)
