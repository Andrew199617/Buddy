<script lang="ts">
	export let state: 'idle' | 'thinking' | 'responding' = 'idle';
	export let size = 60;
	export let label = 'Buddy, your AI companion';
	export let decorative = false;
</script>

<span
	class="buddy-avatar"
	class:thinking={state === 'thinking'}
	class:responding={state === 'responding'}
	style:width={`${size}px`}
	style:height={`${size}px`}
	role={decorative ? undefined : 'img'}
	aria-label={decorative ? undefined : label}
	aria-hidden={decorative ? 'true' : undefined}
	data-buddy-state={state}
>
	<svg viewBox="0 0 100 100" fill="none" aria-hidden="true">
		<circle cx="50" cy="50" r="47" fill="#EDF5EB" />
		<circle cx="50" cy="50" r="45.5" stroke="#27634B" stroke-opacity="0.1" />
		<circle cx="33" cy="30" r="19" fill="white" fill-opacity="0.45" />
		<ellipse cx="50" cy="83" rx="22" ry="3.5" fill="#27634B" fill-opacity="0.09" />
		<g class="buddy-body">
			<g class="buddy-sprout">
				<path d="M50 32C49 24 42 21 38 22C38 29 44 33 50 32Z" fill="#27634B" />
				<path d="M50 31C50 23 57 18 64 20C63 29 57 33 50 31Z" fill="#679C75" />
				<path d="M50 34V27" stroke="#27634B" stroke-width="2" stroke-linecap="round" />
			</g>
			<ellipse cx="22" cy="51" rx="5" ry="9" fill="#A9D9B8" />
			<ellipse cx="78" cy="51" rx="5" ry="9" fill="#A9D9B8" />
			<path
				d="M22 47C22 35 31 29 50 29C69 29 78 35 78 47V57C78 68 70 75 56 76L44 76L32 83V73C25 70 22 65 22 57V47Z"
				fill="#DDF3E4"
				stroke="#27634B"
				stroke-width="2.3"
				stroke-linejoin="round"
			/>
			<path
				d="M30 40C34 35 40 34 46 34"
				stroke="white"
				stroke-opacity="0.8"
				stroke-width="3"
				stroke-linecap="round"
			/>
			<ellipse cx="32" cy="58" rx="5" ry="3" fill="#E89578" fill-opacity="0.45" />
			<ellipse cx="68" cy="58" rx="5" ry="3" fill="#E89578" fill-opacity="0.45" />
			<g class="buddy-gaze">
				<g class="buddy-eyes">
					<ellipse cx="38" cy="50" rx="3" ry="3.7" fill="#244B38" />
					<ellipse cx="62" cy="50" rx="3" ry="3.7" fill="#244B38" />
					<circle cx="39" cy="48.8" r="0.85" fill="white" />
					<circle cx="63" cy="48.8" r="0.85" fill="white" />
				</g>
			</g>
			<g class="buddy-smile">
				<path
					d="M44 60C47 64 53 64 56 60"
					stroke="#27634B"
					stroke-width="2.4"
					stroke-linecap="round"
				/>
				<ellipse class="buddy-speaking-mouth" cx="50" cy="62" rx="4.3" ry="2.8" fill="#27634B" />
			</g>
		</g>
		<circle
			class="buddy-thinking-orbit"
			cx="50"
			cy="50"
			r="45.5"
			stroke="#679C75"
			stroke-width="1.7"
			stroke-linecap="round"
			stroke-dasharray="3 12 3 268"
		/>
		<g class="buddy-sparkle" stroke="#679C75" stroke-width="1.5" stroke-linecap="round">
			<path d="M79 25V31M76 28H82" />
		</g>
	</svg>
</span>

<style>
	.buddy-avatar {
		display: inline-flex;
		flex: none;
		border-radius: 50%;
		background: #edf5eb;
		box-shadow:
			0 0 0 3px rgb(255 255 255 / 65%),
			0 5px 16px rgb(31 62 45 / 9%);
	}

	svg {
		display: block;
		width: 100%;
		height: 100%;
		overflow: visible;
	}

	.buddy-body {
		transform-origin: 50px 70px;
		animation: buddy-breathe 5.6s ease-in-out infinite;
	}

	.buddy-eyes {
		transform-box: fill-box;
		transform-origin: center;
		animation: buddy-blink 7s ease-in-out infinite;
	}

	.buddy-speaking-mouth {
		opacity: 0;
		transform-box: fill-box;
		transform-origin: center;
	}

	.buddy-thinking-orbit {
		opacity: 0;
		transform-origin: 50px 50px;
	}

	.buddy-sparkle {
		opacity: 0.55;
	}

	.thinking .buddy-body {
		animation: buddy-ponder 3.8s ease-in-out infinite;
	}

	.thinking .buddy-gaze {
		transform: translate(1.5px, -2px);
	}

	.thinking .buddy-thinking-orbit {
		opacity: 0.65;
		animation: buddy-orbit 8s linear infinite;
	}

	.thinking .buddy-sprout {
		transform-origin: 50px 32px;
		animation: buddy-sprout 3.8s ease-in-out infinite;
	}

	.responding .buddy-body {
		animation: buddy-respond 2.4s ease-in-out infinite;
	}

	.responding .buddy-speaking-mouth {
		opacity: 1;
		animation: buddy-speak 0.9s ease-in-out infinite;
	}

	.responding .buddy-smile > path {
		opacity: 0;
	}

	.responding .buddy-sparkle {
		animation: buddy-twinkle 2.4s ease-in-out infinite;
	}

	:global(.dark) .buddy-avatar {
		box-shadow:
			0 0 0 3px rgb(221 243 228 / 9%),
			0 5px 18px rgb(0 0 0 / 15%);
	}

	@keyframes buddy-breathe {
		0%,
		100% {
			transform: translateY(0) scale(1);
		}
		50% {
			transform: translateY(-1px) scale(1.015);
		}
	}

	@keyframes buddy-blink {
		0%,
		42%,
		45%,
		74%,
		77%,
		100% {
			transform: scaleY(1);
		}
		43.5%,
		75.5% {
			transform: scaleY(0.1);
		}
	}

	@keyframes buddy-ponder {
		0%,
		100% {
			transform: translateY(0) rotate(-2deg);
		}
		50% {
			transform: translateY(-1.2px) rotate(2deg);
		}
	}

	@keyframes buddy-sprout {
		0%,
		100% {
			transform: rotate(-4deg);
		}
		50% {
			transform: rotate(5deg);
		}
	}

	@keyframes buddy-orbit {
		to {
			transform: rotate(360deg);
		}
	}

	@keyframes buddy-respond {
		0%,
		100% {
			transform: translateY(0);
		}
		50% {
			transform: translateY(-1.5px);
		}
	}

	@keyframes buddy-speak {
		0%,
		100% {
			transform: scaleY(0.55);
		}
		50% {
			transform: scaleY(1);
		}
	}

	@keyframes buddy-twinkle {
		0%,
		100% {
			opacity: 0.35;
		}
		50% {
			opacity: 0.85;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.buddy-avatar svg * {
			animation: none !important;
		}
	}
</style>
