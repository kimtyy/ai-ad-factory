interface ConnectAILabLogoProps {
  className?: string;
  size?: number;
}

export function ConnectAILabLogo({ className = '', size = 18 }: ConnectAILabLogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      {/* 매장 지붕 및 외곽선 */}
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      {/* 십자가(진단/의사 기호) */}
      <line x1="12" y1="9" x2="12" y2="17" />
      <line x1="8" y1="13" x2="16" y2="13" />
    </svg>
  );
}
