import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Bloomberg terminal palette
        base:      '#000000',
        surface:   '#0c1220',
        elevated:  '#111c2e',
        overlay:   '#162238',
        border:    '#1e3050',
        // Text
        primary:   '#dce8f4',
        secondary: '#6b8aaa',
        muted:     '#3d5a78',
        // Accent
        accent:    '#00c2d4',
        'accent-hover': '#00a8b8',
        // Semantic
        profit:    '#00c87a',
        'profit-dim': '#009a5c',
        loss:      '#e82d6b',
        'loss-dim':'#b81a4e',
        warn:      '#f0a800',
        orange:    '#e8721c',
        purple:    '#8b50e8',
        cyan:      '#00a8b8',
        // Regime
        'regime-trending':      '#00c87a',
        'regime-rotation':      '#8b50e8',
        'regime-risk-off':      '#e82d6b',
        'regime-consolidation': '#00a8b8',
      },
      fontFamily: {
        sans: ['IBM Plex Sans', 'system-ui', 'sans-serif'],
        mono: ['IBM Plex Mono', 'Courier New', 'monospace'],
      },
      fontSize: {
        '2xs': '10px',
        xs:    '11px',
        sm:    '12px',
        base:  '13px',
        md:    '14px',
        lg:    '16px',
        xl:    '20px',
        '2xl': '24px',
      },
      spacing: {
        'topbar':    '48px',
        'sidebar':   '200px',
        'statusbar': '28px',
      },
      borderRadius: {
        DEFAULT: '8px',
        sm: '4px',
        lg: '12px',
        xl: '16px',
      },
      animation: {
        'tick-up':   'tickUp 0.6s ease forwards',
        'tick-down': 'tickDown 0.6s ease forwards',
        'pulse-dot': 'pulseDot 2s infinite',
        shimmer:     'shimmer 1.5s infinite',
      },
      keyframes: {
        tickUp:   { '0%': { backgroundColor: 'rgba(0,200,122,0.25)' }, '100%': { backgroundColor: 'transparent' } },
        tickDown: { '0%': { backgroundColor: 'rgba(232,45,107,0.25)' }, '100%': { backgroundColor: 'transparent' } },
        pulseDot: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.4' } },
        shimmer:  { '0%': { backgroundPosition: '200% 0' }, '100%': { backgroundPosition: '-200% 0' } },
      },
    },
  },
  plugins: [],
}

export default config
