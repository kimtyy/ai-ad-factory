import { useState, useEffect, useCallback } from 'react';

const CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+~|}{[]:;?><';

function getRandomChar(): string {
  return CHARS[Math.floor(Math.random() * CHARS.length)];
}

interface ScrambleInProps {
  text: string;
  delay: number;
  triggered: boolean;
}

export function ScrambleIn({ text, delay, triggered }: ScrambleInProps) {
  const [displayText, setDisplayText] = useState<string>('');
  const [hasStarted, setHasStarted] = useState(false);

  useEffect(() => {
    if (!triggered) {
      setDisplayText('\u00A0');
      setHasStarted(false);
      return;
    }

    const timeout = setTimeout(() => {
      setHasStarted(true);
    }, delay);

    return () => clearTimeout(timeout);
  }, [triggered, delay]);

  useEffect(() => {
    if (!hasStarted) return;

    let frame = 0;
    const interval = setInterval(() => {
      frame++;
      const revealIndex = Math.floor(frame * 0.5);

      if (revealIndex >= text.length) {
        setDisplayText(text);
        clearInterval(interval);
        return;
      }

      let result = '';
      for (let i = 0; i < text.length; i++) {
        if (text[i] === ' ') {
          result += ' ';
        } else if (i < revealIndex) {
          result += text[i];
        } else if (i < revealIndex + 3) {
          result += getRandomChar();
        } else {
          result += '\u00A0';
        }
      }
      setDisplayText(result);
    }, 25);

    return () => clearInterval(interval);
  }, [hasStarted, text]);

  if (!triggered) {
    return <span>{'\u00A0'}</span>;
  }

  return <span>{displayText || '\u00A0'}</span>;
}

interface ScrambleTextProps {
  text: string;
  isHovered: boolean;
  className?: string;
}

export function ScrambleText({ text, isHovered, className }: ScrambleTextProps) {
  const [displayText, setDisplayText] = useState(text);

  const scramble = useCallback(() => {
    let frame = 0;
    const interval = setInterval(() => {
      frame++;
      const revealIndex = Math.floor(frame / 4);

      if (revealIndex >= text.length) {
        setDisplayText(text);
        clearInterval(interval);
        return;
      }

      let result = '';
      for (let i = 0; i < text.length; i++) {
        if (text[i] === ' ') {
          result += ' ';
        } else if (i < revealIndex) {
          result += text[i];
        } else {
          result += getRandomChar();
        }
      }
      setDisplayText(result);
    }, 25);

    return () => clearInterval(interval);
  }, [text]);

  useEffect(() => {
    if (isHovered) {
      const cleanup = scramble();
      return cleanup;
    } else {
      setDisplayText(text);
    }
  }, [isHovered, scramble, text]);

  return <span className={className}>{displayText}</span>;
}
