import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;

function Svg({ children, ...p }: P) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...p}
    >
      {children}
    </svg>
  );
}

export const Shield = (p: P) => (
  <Svg {...p}>
    <path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3Z" />
    <path d="m9 12 2 2 4-4" />
  </Svg>
);
export const ShieldOff = (p: P) => (
  <Svg {...p}>
    <path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3Z" />
    <path d="m9.5 9.5 5 5m0-5-5 5" />
  </Svg>
);
export const Alert = (p: P) => (
  <Svg {...p}>
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9v4m0 4h.01" />
  </Svg>
);
export const Check = (p: P) => (
  <Svg {...p}>
    <path d="m5 12 5 5L20 7" />
  </Svg>
);
export const Cross = (p: P) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);
export const Lock = (p: P) => (
  <Svg {...p}>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </Svg>
);
export const Globe = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
  </Svg>
);
export const Scissors = (p: P) => (
  <Svg {...p}>
    <circle cx="6" cy="6" r="3" />
    <circle cx="6" cy="18" r="3" />
    <path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12" />
  </Svg>
);
export const Brain = (p: P) => (
  <Svg {...p}>
    <rect x="4" y="4" width="16" height="16" rx="3" />
    <path d="M9 9h6v6H9z" />
    <path d="M9 2v2m6-2v2M9 20v2m6-2v2M2 9h2m-2 6h2m16-6h2m-2 6h2" />
  </Svg>
);
export const Gavel = (p: P) => (
  <Svg {...p}>
    <path d="m14 13-7.5 7.5a2.1 2.1 0 0 1-3-3L11 10" />
    <path d="m16 16 6-6M8 8l6-6M9 7l8 8M21 11l-8-8" />
  </Svg>
);
export const Route = (p: P) => (
  <Svg {...p}>
    <circle cx="6" cy="19" r="3" />
    <circle cx="18" cy="5" r="3" />
    <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" />
  </Svg>
);
export const Message = (p: P) => (
  <Svg {...p}>
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </Svg>
);
export const Bolt = (p: P) => (
  <Svg {...p}>
    <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z" />
  </Svg>
);
export const Play = (p: P) => (
  <Svg {...p}>
    <path d="M7 4v16l13-8L7 4Z" fill="currentColor" />
  </Svg>
);
export const Download = (p: P) => (
  <Svg {...p}>
    <path d="M12 3v12m0 0-4-4m4 4 4-4M4 21h16" />
  </Svg>
);
export const Upload = (p: P) => (
  <Svg {...p}>
    <path d="M12 21V9m0 0-4 4m4-4 4 4M4 3h16" />
  </Svg>
);
export const Flask = (p: P) => (
  <Svg {...p}>
    <path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3" />
    <path d="M7 15h10" />
  </Svg>
);
