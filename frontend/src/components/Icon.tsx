import type { ReactNode } from "react";

interface IconProps {
  size?: number;
}

function Svg({ size = 16, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export function ArrowUpIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="m5 12 7-7 7 7" />
      <path d="M12 19V5" />
    </Svg>
  );
}

export function StopIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function CheckIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="M20 6 9 17l-5-5" />
    </Svg>
  );
}

export function AlertIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </Svg>
  );
}

export function RetryIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
    </Svg>
  );
}

export function ArrowDownIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="M12 5v14" />
      <path d="m19 12-7 7-7-7" />
    </Svg>
  );
}

export function SparkIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="M12 2v20" />
      <path d="M2 12h20" />
      <path d="m4.93 4.93 14.14 14.14" />
      <path d="m19.07 4.93-14.14 14.14" />
    </Svg>
  );
}

export function UserIcon({ size }: IconProps) {
  return (
    <Svg size={size}>
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </Svg>
  );
}
