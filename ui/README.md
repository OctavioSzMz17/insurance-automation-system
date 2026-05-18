# Insurance Claims Portal UI

A modern, glassmorphism-styled Next.js frontend for submitting and tracking insurance claims powered by AI agents.

## Features

- **Glassmorphism Design**: Liquid glass effects, blur, and custom CSS animations
- **PDF Upload**: Drag-and-drop claim document submission
- **Real-time Processing**: Submit claims and get instant AI-driven decisions
- **Reasoning Trail**: View complete chain-of-thought from analyst and auditor agents
- **Responsive**: Works on desktop and mobile devices
- **Smooth Animations**: Framer Motion transitions and Tailwind CSS effects

## Tech Stack

- **Framework**: Next.js 14 (React 18)
- **Styling**: Tailwind CSS 3 + Custom CSS (glassmorphism)
- **Animation**: Framer Motion
- **Forms**: React Hook Form + Zod validation
- **HTTP**: Axios
- **Icons**: Lucide React

## Getting Started

### Prerequisites

- Node.js 16+ (or use `nvm`)
- npm or yarn
- Running FastAPI backend on `http://localhost:8000`

### Installation

```bash
# Install dependencies
npm install

# Copy environment example
cp .env.local.example .env.local

# (Optional) Update .env.local if your backend runs on a different URL
```

### Development

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Production Build

```bash
npm run build
npm start
```

## Project Structure

```
ui/
├── src/
│   ├── app/
│   │   ├── layout.tsx              # Root layout
│   │   └── page.tsx                # Main page (form + result)
│   ├── components/
│   │   ├── GlassCard.tsx           # Reusable frosted glass container
│   │   ├── ClaimForm.tsx           # Claim submission form
│   │   ├── LiquidButton.tsx        # Animated gradient button
│   │   ├── ResultCard.tsx          # Result display
│   │   ├── StatusBadge.tsx         # Status/severity badges
│   │   └── ReasoningTimeline.tsx   # Agent reasoning steps
│   ├── lib/
│   │   └── api.ts                  # API client + types
│   └── styles/
│       └── globals.css             # Glassmorphism effects
├── package.json
├── tsconfig.json
├── tailwind.config.js
└── next.config.js
```

## Environment Variables

Create `.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

If your FastAPI backend runs on a different host/port, update this URL.

## API Integration

The UI communicates with the FastAPI orchestrator via these endpoints:

- `POST /claims/submit` - Submit a claim with PDF + incident data
- `GET /claims/{claim_id}` - Fetch claim details (future)
- `GET /health` - Health check

### Expected Response Format

```json
{
  "claim_id": "uuid-string",
  "user_id": 123,
  "policy_id": "POL-123",
  "severity_label": "LOW|HIGH|TOTAL_LOSS",
  "claim_status": "APPROVED|REJECTED|MANUAL_REVIEW",
  "ai_reasoning": "Full reasoning from agents",
  "analyst_reasoning": "Analyst extraction notes",
  "auditor_reasoning": "Auditor decision notes",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

## Design Details

### Glassmorphism Effects

- **Backdrop Blur**: `backdrop-filter: blur(20px)` on glass containers
- **Semi-transparent Backgrounds**: RGBA colors with 10-15% opacity
- **Border Gradients**: Purple-to-blue gradient accents
- **Liquid Glass Buttons**: Animated gradient borders with shimmer effect
- **Smooth Transitions**: Framer Motion for all interactive elements

### Color Palette

- **Background**: Deep navy/slate (`#0f172a`)
- **Glass**: White with 10-15% opacity
- **Accents**: Purple (`#a855f7`) and Blue (`#3b82f6`)
- **Status**: Green (approved), Red (rejected), Amber (manual review)

### Animations

- `fade-in`: Element entrance
- `shimmer`: Loading state
- `liquid-pulse`: Button hover glow
- `slide-down`: Expandable timeline items

## Customization

### Change Colors

Edit `src/styles/globals.css` and `tailwind.config.js`:

```css
/* Update gradient colors */
body {
  background: linear-gradient(135deg, #your-color-1 0%, #your-color-2 100%);
}
```

### Adjust Blur Amount

Change `backdrop-blur-lg` in `tailwind.config.js`:

```js
backdropBlur: {
  lg: '40px', // Increase for more blur
}
```

### Add Custom Effects

Add new CSS to `src/styles/globals.css`:

```css
.custom-effect {
  background: linear-gradient(...);
  filter: /* your effects */;
  animation: /* your animation */;
}
```

## Troubleshooting

### CORS Errors

If you see CORS errors, your backend needs to allow requests from `http://localhost:3000`:

**FastAPI backend** (`ai-orchestrator/app.py`):

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### PDF Upload Not Working

Ensure your FastAPI `/claims/submit` endpoint accepts `multipart/form-data` with:

- `pdf` (file)
- `user_id` (integer)
- `incident_date` (string)
- `incident_description` (string)
- `incident_location` (string)

### Backend Unreachable

Check that:
1. FastAPI is running on `http://localhost:8000`
2. `.env.local` has the correct `NEXT_PUBLIC_API_URL`
3. No firewall/network blocking
4. Backend CORS is configured correctly

## Deployment

### Docker

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY .next ./.next
EXPOSE 3000
CMD ["npm", "start"]
```

### Vercel

```bash
vercel
```

Set environment variables in Vercel dashboard:

```
NEXT_PUBLIC_API_URL=https://your-backend-domain.com
```

## Contributing

- Keep components small and focused
- Use TypeScript for type safety
- Follow the established glassmorphism design patterns
- Test on mobile devices for responsiveness

## License

Proprietary - Insurance Automation System
