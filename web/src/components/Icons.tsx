import type { ReactNode } from 'react'

function Icon({ children, size = 18, strokeWidth = 1.8 }: { children: ReactNode; size?: number; strokeWidth?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{children}</svg>
  )
}

type IconProps = { size?: number }

export const SendIcon = (props: IconProps) => <Icon {...props}><path d="M12 19V5M5.5 11.5L12 5l6.5 6.5" /></Icon>
export const PlusIcon = (props: IconProps) => <Icon {...props}><path d="M12 5v14M5 12h14" /></Icon>
export const CloseIcon = (props: IconProps) => <Icon {...props}><path d="M6 6l12 12M18 6L6 18" /></Icon>
export const CheckIcon = (props: IconProps) => <Icon {...props} strokeWidth={2.2}><path d="M5 12.5l4.5 4.5L19 7" /></Icon>
export const ExternalIcon = (props: IconProps) => <Icon {...props}><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5" /></Icon>
export const CopyIcon = (props: IconProps) => <Icon {...props}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 012-2h9" /></Icon>
export const FileIcon = (props: IconProps) => <Icon {...props}><path d="M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6" /></Icon>
export const LayersIcon = (props: IconProps) => <Icon {...props}><path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5" /></Icon>
export const RepoIcon = (props: IconProps) => <Icon {...props}><path d="M5 4h12a2 2 0 012 2v13H7a2 2 0 01-2-2zM5 17a2 2 0 012-2h12M9 8h6" /></Icon>
export const ChatPlusIcon = (props: IconProps) => <Icon {...props}><path d="M5 4h14v12H9l-4 4zM12 7.5v5M9.5 10h5" /></Icon>
export const SparkIcon = (props: IconProps) => <Icon {...props}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z" /></Icon>
export const CompassIcon = (props: IconProps) => <Icon {...props}><circle cx="12" cy="12" r="9" /><path d="M15.5 8.5l-2 5-5 2 2-5z" /></Icon>
export const BranchIcon = (props: IconProps) => <Icon {...props}><circle cx="6" cy="6" r="2" /><circle cx="6" cy="18" r="2" /><circle cx="18" cy="8" r="2" /><path d="M6 8v8M18 10c0 4-6 3-12 6" /></Icon>
export const ShieldIcon = (props: IconProps) => <Icon {...props}><path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z" /><path d="M9 12l2.2 2.2L15.5 10" /></Icon>
export const GraphIcon = (props: IconProps) => <Icon {...props}><circle cx="6" cy="12" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="18" cy="18" r="2.2" /><path d="M8 11l8-4M8 13l8 4" /></Icon>
export const ClockIcon = (props: IconProps) => <Icon {...props}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></Icon>

/** The product mark, also used as the assistant avatar. */
export function LogoMark({ size = 22 }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" focusable="false">
      <path d="M4 4h6l2 2 2-2h6v15h-6l-2 2-2-2H4zM12 6v15" />
    </svg>
  )
}
