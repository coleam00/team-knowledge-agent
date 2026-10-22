import type { AnswerResponse, Citation } from '../src/components/Answer'

export const deployCitation: Citation = {
  n: 1,
  source_id: 'handbook',
  repo: 'tka-fixtures/handbook',
  path: 'docs/deploy.md',
  start_line: 14,
  end_line: 18,
  commit: 'C',
  url: 'https://github.com/tka-fixtures/handbook/blob/C/docs/deploy.md#L14-L18',
  snippet: '## Deploy freeze\nDeploys are frozen on Fridays after 14:00 UTC.\n\nResume deployments on Monday.\nAsk the on-call engineer if unsure.',
}

export const longCitation: Citation = {
  ...deployCitation,
  n: 2,
  path: 'docs/platform/operations/production/regional-services/release-management/emergency-deployment-procedure.md',
  start_line: 80,
  end_line: 87,
  url: 'https://github.com/tka-fixtures/handbook/blob/C/docs/platform/operations/production/regional-services/release-management/emergency-deployment-procedure.md#L80-L87',
  snippet: '## Release checklist\nCheck the service dashboard.\nConfirm that the freeze has ended.\nNotify the on-call engineer.\nDeploy one region at a time.\nWatch the health checks.\nRecord the release.\nClose the ticket.',
}

export const literalText = '<script>window.citationExecuted = true</script>\n<img src=x onerror="window.citationExecuted = true">'

export const citedAnswer = {
  known: true,
  answer: 'Deploys are frozen on Fridays after 14:00 UTC. [1]\nCheck the release checklist before deploying again. [2] An unmatched marker stays plain text: [99].',
  citations: [deployCitation, longCitation],
} satisfies AnswerResponse
