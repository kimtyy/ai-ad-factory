import { motion } from 'framer-motion';
import { ConnectAILabLogo } from './ConnectAILabLogo';
import { SITE_CONFIG } from '../config/content';

interface NavbarProps {
  entranceComplete: boolean;
}

export function Navbar({ entranceComplete }: NavbarProps) {
  return (
    <motion.nav
      className="fixed top-0 left-0 right-0 z-50 h-20 flex items-center justify-between px-4 sm:px-6 md:px-8"
      initial={{ opacity: 0 }}
      animate={{ opacity: entranceComplete ? 1 : 0 }}
      transition={{ duration: 0.8 }}
    >
      <a href="/" className="h-10 sm:h-12 px-4 sm:px-5 bg-white/15 backdrop-blur-md rounded-[14px] flex items-center gap-2.5 no-underline">
        <ConnectAILabLogo size={18} className="text-white" />
        <span className="text-[14px] sm:text-[16px] font-medium tracking-tight text-white">{SITE_CONFIG.brandName}</span>
      </a>
      <a
        href={SITE_CONFIG.appUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="h-10 sm:h-12 px-5 sm:px-6 bg-white rounded-full flex items-center text-black text-[14px] sm:text-[16px] font-medium no-underline hover:bg-zinc-200 transition-colors"
      >
        시작하기
      </a>
    </motion.nav>
  );
}
