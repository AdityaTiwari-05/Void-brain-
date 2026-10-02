/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Indore Cyber Cell dark theme — navy/cream/gold palette from reference UI
        navy: {
          950: '#0a0e1a',
          900: '#0d1526',
          800: '#111e38',
          700: '#162548',
          600: '#1a2d5a',
        },
        gold: {
          600: '#c9a227',
          500: '#d4af37',
          400: '#e8c547',
          300: '#f0d060',
        },
        cream: {
          100: '#fdf8f0',
          200: '#f5edd8',
          300: '#ecdfc2',
        },
        risk: {
          high:   '#ef4444',
          medium: '#f59e0b',
          low:    '#22c55e',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
      },
    },
  },
  plugins: [],
}
