		const desktop = document.getElementById("desktop");
		const emptyHint = document.getElementById("empty-hint");
		const launcherForm = document.getElementById("launcher-form");
		const launcherInput = document.getElementById("launcher-input");
		const launcherButton = document.getElementById("launcher-button");
		const globalStatus = document.getElementById("global-status");
		const dock = document.getElementById("dock");
		const errorToast = document.getElementById("error-toast");
		const schedulerWidget = document.getElementById("scheduler-widget");
		const schedulerToggle = document.getElementById("scheduler-toggle");
		const schedulerDot = document.getElementById("scheduler-dot");
		const schedulerCount = document.getElementById("scheduler-count");
		const schedulerPanel = document.getElementById("scheduler-panel");
		const schedulerUpdated = document.getElementById("scheduler-updated");
		const schedulerRunning = document.getElementById("scheduler-running");
		const schedulerUpcoming = document.getElementById("scheduler-upcoming");
		const schedulerRecent = document.getElementById("scheduler-recent");
		const nodeCanvas = document.getElementById("node-field");
		const nodeContext = nodeCanvas.getContext("2d");

		const windows = new Map();
		const HEARTBEAT_INTERVAL_MS = 90000;
		const SCHEDULER_INTERVAL_MS = 15000;
		const NODE_PULSE_INTERVAL_MS = 2500;
		const NODE_NEIGHBOR_INTENSITY = 0.125;
		const NODE_SECONDARY_INTENSITY = NODE_NEIGHBOR_INTENSITY / 2;
		const NODE_LIGHTNING_BRANCHES = 4;
		let windowCount = 0;
		let topZ = 20;
		let toastTimer = 0;
		let backendAlive = true;
		let activeStatus = "Idle";
		let pointerTargetX = 0;
		let pointerTargetY = 0;
		let pointerCurrentX = 0;
		let pointerCurrentY = 0;
		let nodeWidth = 0;
		let nodeHeight = 0;
		let nodePixelRatio = 1;
		let nodes = [];
		let nodeEdges = [];
		let nodePulseTimer = 0;

		function setGlobalStatus(value) {
			activeStatus = value;
			globalStatus.textContent = backendAlive ? value : "Offline";
		}

		function updatePointerTarget(clientX, clientY) {
			const halfWidth = Math.max(window.innerWidth / 2, 1);
			const halfHeight = Math.max(window.innerHeight / 2, 1);
			pointerTargetX = Math.max(-1, Math.min(1, (clientX - halfWidth) / halfWidth));
			pointerTargetY = Math.max(-1, Math.min(1, (clientY - halfHeight) / halfHeight));
		}

		function animateBackgroundDrift() {
			pointerCurrentX += (pointerTargetX - pointerCurrentX) * 0.035;
			pointerCurrentY += (pointerTargetY - pointerCurrentY) * 0.035;

			drawNodeField(performance.now());

			window.requestAnimationFrame(animateBackgroundDrift);
		}

		function resizeNodeField() {
			nodePixelRatio = Math.min(window.devicePixelRatio || 1, 2);
			nodeWidth = window.innerWidth;
			nodeHeight = window.innerHeight;
			nodeCanvas.width = Math.floor(nodeWidth * nodePixelRatio);
			nodeCanvas.height = Math.floor(nodeHeight * nodePixelRatio);
			nodeCanvas.style.width = `${nodeWidth}px`;
			nodeCanvas.style.height = `${nodeHeight}px`;
			nodeContext.setTransform(nodePixelRatio, 0, 0, nodePixelRatio, 0, 0);
			createNodeField();
		}

		function createNodeField() {
			const area = nodeWidth * nodeHeight;
			const targetCount = Math.max(72, Math.min(160, Math.floor(area / 11500)));
			nodes = Array.from({ length: targetCount }, (_, index) => {
				const columnCount = Math.ceil(Math.sqrt(targetCount * (nodeWidth / Math.max(nodeHeight, 1))));
				const rowCount = Math.ceil(targetCount / columnCount);
				const column = index % columnCount;
				const row = Math.floor(index / columnCount);
				const cellWidth = nodeWidth / columnCount;
				const cellHeight = nodeHeight / rowCount;
				const baseX = (column + 0.5) * cellWidth + (Math.random() - 0.5) * cellWidth * 0.7;
				const baseY = (row + 0.5) * cellHeight + (Math.random() - 0.5) * cellHeight * 0.7;

				return {
					x: baseX,
					y: baseY,
					baseX,
					baseY,
					phase: Math.random() * Math.PI * 2,
					radius: 1 + Math.random() * 1.4,
					intensity: 0,
					neighbors: [],
				};
			});

			nodeEdges = [];
			for (let index = 0; index < nodes.length; index += 1) {
				const nearest = nodes
					.map((node, candidateIndex) => ({
						candidateIndex,
						distance: Math.hypot(nodes[index].baseX - node.baseX, nodes[index].baseY - node.baseY),
					}))
					.filter((item) => item.candidateIndex !== index)
					.sort((a, b) => a.distance - b.distance)
					.slice(0, 4);

				nearest.forEach(({ candidateIndex, distance }) => {
					const a = Math.min(index, candidateIndex);
					const b = Math.max(index, candidateIndex);
					if (nodeEdges.some((edge) => edge.a === a && edge.b === b)) return;
					nodeEdges.push({ a, b, distance });
					nodes[a].neighbors.push(b);
					nodes[b].neighbors.push(a);
				});
			}
		}

		function pulseRandomNode() {
			if (!backendAlive || !nodes.length) return;
			const index = Math.floor(Math.random() * nodes.length);
			nodes[index].intensity = 1;

			const firstHop = [...nodes[index].neighbors]
				.sort(() => Math.random() - 0.5)
				.slice(0, NODE_LIGHTNING_BRANCHES);

			firstHop.forEach((neighborIndex, branchIndex) => {
				window.setTimeout(() => {
					nodes[neighborIndex].intensity = Math.max(nodes[neighborIndex].intensity, NODE_NEIGHBOR_INTENSITY);

					const secondHop = nodes[neighborIndex].neighbors
						.filter((candidateIndex) => candidateIndex !== index)
						.sort(() => Math.random() - 0.5)
						.slice(0, 2);

					secondHop.forEach((candidateIndex, secondIndex) => {
						window.setTimeout(() => {
							nodes[candidateIndex].intensity = Math.max(nodes[candidateIndex].intensity, NODE_SECONDARY_INTENSITY);
						}, 38 + secondIndex * 34);
					});
				}, branchIndex * 42);
			});
		}

		function drawNodeField(time) {
			if (!nodeWidth || !nodeHeight) return;

			nodeContext.clearRect(0, 0, nodeWidth, nodeHeight);
			const mouseX = nodeWidth / 2 + pointerCurrentX * nodeWidth / 2;
			const mouseY = nodeHeight / 2 + pointerCurrentY * nodeHeight / 2;
			const repelRadius = Math.min(nodeWidth, nodeHeight) * 0.38;
			const baseLine = backendAlive ? 0.065 : 0.025;
			const baseNode = backendAlive ? 0.105 : 0.045;

			nodes.forEach((node) => {
				const dx = node.baseX - mouseX;
				const dy = node.baseY - mouseY;
				const distance = Math.max(Math.hypot(dx, dy), 1);
				const repel = Math.max(0, 1 - distance / repelRadius);
				const driftX = Math.cos(time * 0.00012 + node.phase) * 4;
				const driftY = Math.sin(time * 0.00014 + node.phase) * 4;
				node.x = node.baseX + (dx / distance) * repel * 22 + driftX;
				node.y = node.baseY + (dy / distance) * repel * 18 + driftY;
				node.intensity *= 0.965;
				if (node.intensity < 0.002) {
					node.intensity = 0;
				}
			});

			nodeEdges.forEach((edge) => {
				const a = nodes[edge.a];
				const b = nodes[edge.b];
				const pulse = Math.max(a.intensity, b.intensity);
				const alpha = Math.min(0.54, baseLine + pulse * 0.46);
				nodeContext.beginPath();
				nodeContext.moveTo(a.x, a.y);
				nodeContext.lineTo(b.x, b.y);
				nodeContext.strokeStyle = `rgba(69, 114, 126, ${alpha})`;
				nodeContext.lineWidth = 0.62 + pulse * 1.35;
				nodeContext.stroke();
			});

			nodes.forEach((node) => {
				const alpha = Math.min(0.86, baseNode + node.intensity * 0.74);
				const radius = node.radius + node.intensity * 3.5;
				nodeContext.beginPath();
				nodeContext.arc(node.x, node.y, radius, 0, Math.PI * 2);
				nodeContext.fillStyle = `rgba(90, 139, 148, ${alpha})`;
				nodeContext.fill();

				if (node.intensity > 0.05) {
					const glow = nodeContext.createRadialGradient(node.x, node.y, 0, node.x, node.y, 18 + node.intensity * 18);
					glow.addColorStop(0, `rgba(111, 183, 190, ${0.24 * node.intensity})`);
					glow.addColorStop(1, "rgba(111, 183, 190, 0)");
					nodeContext.beginPath();
					nodeContext.arc(node.x, node.y, 18 + node.intensity * 18, 0, Math.PI * 2);
					nodeContext.fillStyle = glow;
					nodeContext.fill();
				}
			});
		}

		function setBackendAlive(value) {
			if (backendAlive === value) return;
			backendAlive = value;
			document.body.classList.toggle("backend-offline", !value);
			globalStatus.textContent = value ? activeStatus : "Offline";
		}

		function confirmHeartbeat() {
			pulseRandomNode();
		}

		function formatScheduleTime(value) {
			if (!value) return "No next run";
			const date = new Date(value);
			if (Number.isNaN(date.getTime())) return value;
			return date.toLocaleString([], {
				month: "short",
				day: "numeric",
				hour: "2-digit",
				minute: "2-digit",
			});
		}

		function toolLabelFromSchedule(item) {
			const calls = Array.isArray(item.tool_calls) ? item.tool_calls : [];
			if (!calls.length) return "No tool calls";
			const names = calls.map((call) => call.tool_name).filter(Boolean);
			return names.length > 1 ? `${names[0]} + ${names.length - 1}` : names[0];
		}

		function renderSchedulerList(container, items, emptyText, renderItem) {
			container.innerHTML = "";
			if (!items.length) {
				const empty = document.createElement("div");
				empty.className = "scheduler-empty";
				empty.textContent = emptyText;
				container.appendChild(empty);
				return;
			}

			items.forEach((item) => {
				const row = document.createElement("div");
				row.className = "scheduler-row";
				const rendered = renderItem(item);
				row.innerHTML = `
					<div class="scheduler-row-main"></div>
					<div class="scheduler-row-meta"></div>
				`;
				row.querySelector(".scheduler-row-main").textContent = rendered.main;
				row.querySelector(".scheduler-row-meta").textContent = rendered.meta;
				container.appendChild(row);
			});
		}

		function updateSchedulerStatus(data) {
			const running = Array.isArray(data.running) ? data.running : [];
			const scheduled = Array.isArray(data.scheduled) ? data.scheduled : [];
			const recent = Array.isArray(data.recent) ? data.recent : [];
			const activeCount = running.length + scheduled.length;

			schedulerCount.textContent = String(activeCount);
			schedulerWidget.classList.toggle("has-running", running.length > 0);
			schedulerDot.title = running.length > 0 ? "Scheduler is running jobs" : "Scheduler idle";
			schedulerUpdated.textContent = `Updated ${formatScheduleTime(data.now)}`;

			renderSchedulerList(
				schedulerRunning,
				running,
				"No running jobs",
				(item) => ({
					main: item.tool_name || "Scheduled job",
					meta: item.status || "queued",
				}),
			);

			renderSchedulerList(
				schedulerUpcoming,
				scheduled,
				"No upcoming schedules",
				(item) => ({
					main: toolLabelFromSchedule(item),
					meta: `${formatScheduleTime(item.next_run_at)}${item.repeat ? ` - ${item.repeat}` : ""}`,
				}),
			);

			renderSchedulerList(
				schedulerRecent,
				recent,
				"No recent jobs",
				(item) => ({
					main: item.tool_name || "Scheduler",
					meta: `${item.status}${item.finished_at ? ` - ${formatScheduleTime(item.finished_at)}` : ""}`,
				}),
			);
		}

		async function refreshSchedulerStatus() {
			try {
				const response = await fetch("/scheduler/status", {
					cache: "no-store",
					headers: { "Accept": "application/json" },
				});
				if (!response.ok) {
					throw new Error(`Scheduler failed with status ${response.status}`);
				}
				updateSchedulerStatus(await response.json());
			} catch (error) {
				schedulerWidget.classList.remove("has-running");
				schedulerCount.textContent = "!";
				schedulerUpdated.textContent = "Unavailable";
			}
		}

		async function checkHeartbeat() {
			try {
				const response = await fetch("/heartbeat", {
					cache: "no-store",
					headers: { "Accept": "application/json" },
				});
				if (!response.ok) {
					throw new Error(`Heartbeat failed with status ${response.status}`);
				}
				const data = await response.json();
				setBackendAlive(data.alive === true);
				if (data.alive === true) {
					confirmHeartbeat();
				}
			} catch (error) {
				setBackendAlive(false);
			}
		}

		function showError(message) {
			window.clearTimeout(toastTimer);
			errorToast.textContent = message;
			errorToast.classList.add("visible");
			toastTimer = window.setTimeout(() => {
				errorToast.classList.remove("visible");
			}, 5200);
		}

		function titleFromPrompt(prompt) {
			const trimmed = prompt.replace(/\s+/g, " ").trim();
			return trimmed.length > 56 ? `${trimmed.slice(0, 55)}...` : trimmed;
		}

		function updateEmptyState() {
			emptyHint.classList.toggle("hidden", windows.size > 0);
		}

		function focusWindow(state) {
			topZ += 1;
			state.el.style.zIndex = topZ;
			document.querySelectorAll(".window.active").forEach((node) => node.classList.remove("active"));
			state.el.classList.add("active");
		}

		function setWindowStatus(state, status) {
			state.status = status;
			state.subtitle.textContent = `${status} - ${state.messages.length} messages`;
		}

		function appendBubble(state, role, text) {
			const bubble = document.createElement("div");
			bubble.className = `bubble markdown ${role === "user" ? "user" : "agent"}`;
			bubble.innerHTML = renderMarkdown(text || "(No response text returned.)");
			enrichLocalMedia(bubble);
			state.messagesView.appendChild(bubble);
			state.body.scrollTop = state.body.scrollHeight;
		}

		function escapeHtml(value) {
			return String(value)
				.replaceAll("&", "&amp;")
				.replaceAll("<", "&lt;")
				.replaceAll(">", "&gt;")
				.replaceAll('"', "&quot;")
				.replaceAll("'", "&#39;");
		}

		function renderInlineMarkdown(value) {
			let html = escapeHtml(value);
			html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
			html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
			html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
			html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
			html = html.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
			html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
			return html;
		}

		function renderListBlock(lines, ordered) {
			const tag = ordered ? "ol" : "ul";
			const items = lines.map((line) => {
				const text = ordered
					? line.replace(/^\s*\d+\.\s+/, "")
					: line.replace(/^\s*[-*]\s+/, "");
				return `<li>${renderInlineMarkdown(text)}</li>`;
			});
			return `<${tag}>${items.join("")}</${tag}>`;
		}

		function renderMarkdown(value) {
			const source = String(value || "");
			const blocks = [];
			const lines = source.replace(/\r\n/g, "\n").split("\n");
			let paragraph = [];
			let list = [];
			let listOrdered = false;
			let quote = [];
			let code = [];
			let inCode = false;

			function flushParagraph() {
				if (!paragraph.length) return;
				blocks.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
				paragraph = [];
			}

			function flushList() {
				if (!list.length) return;
				blocks.push(renderListBlock(list, listOrdered));
				list = [];
			}

			function flushQuote() {
				if (!quote.length) return;
				blocks.push(`<blockquote>${renderInlineMarkdown(quote.join(" "))}</blockquote>`);
				quote = [];
			}

			function flushCode() {
				if (!code.length) return;
				blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
				code = [];
			}

			for (const line of lines) {
				if (line.trim().startsWith("```")) {
					if (inCode) {
						flushCode();
						inCode = false;
					} else {
						flushParagraph();
						flushList();
						flushQuote();
						inCode = true;
					}
					continue;
				}

				if (inCode) {
					code.push(line);
					continue;
				}

				if (!line.trim()) {
					flushParagraph();
					flushList();
					flushQuote();
					continue;
				}

				const heading = line.match(/^(#{1,3})\s+(.+)$/);
				if (heading) {
					flushParagraph();
					flushList();
					flushQuote();
					const level = heading[1].length;
					blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
					continue;
				}

				const isOrdered = /^\s*\d+\.\s+/.test(line);
				const isUnordered = /^\s*[-*]\s+/.test(line);
				if (isOrdered || isUnordered) {
					flushParagraph();
					flushQuote();
					if (list.length && listOrdered !== isOrdered) {
						flushList();
					}
					listOrdered = isOrdered;
					list.push(line);
					continue;
				}

				if (/^\s*>\s?/.test(line)) {
					flushParagraph();
					flushList();
					quote.push(line.replace(/^\s*>\s?/, ""));
					continue;
				}

				flushList();
				flushQuote();
				paragraph.push(line.trim());
			}

			if (inCode) {
				flushCode();
			}
			flushParagraph();
			flushList();
			flushQuote();

			return blocks.join("");
		}

		function internalFileUrl(path) {
			const normalized = String(path || "")
				.replaceAll("\\", "/")
				.replace(/^\.?\//, "")
				.replace(/^internal\//, "");
			return `/internal_file/${normalized.split("/").map(encodeURIComponent).join("/")}`;
		}

		function mediaTypeFromPath(path) {
			const extension = String(path).split(".").pop().toLowerCase();
			if (["jpg", "jpeg", "png", "gif", "webp"].includes(extension)) return "image";
			if (["mp4", "webm", "mov", "avi", "mkv"].includes(extension)) return "video";
			return "";
		}

		function enrichLocalMedia(container) {
			const pathPattern = /(^|[\s"'`([{])((?!https?:\/\/)(?:[A-Za-z0-9_.-]+[\\/]){1,8}[A-Za-z0-9_. @%()+-]+\.(?:jpe?g|png|gif|webp|mp4|webm|mov|avi|mkv))(?=$|[\s"'`)\]},.:;])/gi;
			const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
				acceptNode(node) {
					const parent = node.parentElement;
					if (!parent || parent.closest("a, code, pre, .media-preview")) {
						return NodeFilter.FILTER_REJECT;
					}
					pathPattern.lastIndex = 0;
					return pathPattern.test(node.nodeValue || "")
						? NodeFilter.FILTER_ACCEPT
						: NodeFilter.FILTER_REJECT;
				},
			});
			const nodes = [];
			while (walker.nextNode()) nodes.push(walker.currentNode);

			const previewed = new Set();
			nodes.forEach((node) => {
				const text = node.nodeValue || "";
				const fragment = document.createDocumentFragment();
				let cursor = 0;
				pathPattern.lastIndex = 0;
				let match;

				while ((match = pathPattern.exec(text)) !== null) {
					const fullMatch = match[0];
					const prefix = match[1] || "";
					const rawPath = match[2];
					const pathStart = match.index + prefix.length;
					const pathEnd = pathStart + rawPath.length;

					fragment.appendChild(document.createTextNode(text.slice(cursor, pathStart)));

					const normalizedPath = rawPath.replaceAll("\\", "/").replace(/^\.?\//, "").replace(/^internal\//, "");
					const href = internalFileUrl(normalizedPath);
					const link = document.createElement("a");
					link.href = href;
					link.target = "_blank";
					link.rel = "noopener noreferrer";
					link.textContent = rawPath;
					fragment.appendChild(link);

					const type = mediaTypeFromPath(normalizedPath);
					if (type && !previewed.has(normalizedPath)) {
						previewed.add(normalizedPath);
						const preview = document.createElement("div");
						preview.className = "media-preview";
						if (type === "image") {
							const image = document.createElement("img");
							image.src = href;
							image.alt = normalizedPath.split("/").pop() || "image result";
							image.loading = "lazy";
							preview.appendChild(image);
						} else {
							const video = document.createElement("video");
							video.src = href;
							video.controls = true;
							video.preload = "metadata";
							preview.appendChild(video);
						}
						fragment.appendChild(preview);
					}

					cursor = pathEnd;
					if (fullMatch.length > prefix.length + rawPath.length) {
						fragment.appendChild(document.createTextNode(fullMatch.slice(prefix.length + rawPath.length)));
						cursor = match.index + fullMatch.length;
					}
				}

				fragment.appendChild(document.createTextNode(text.slice(cursor)));
				node.replaceWith(fragment);
			});
		}

		function buildTaskGroups(tasks) {
			const groups = [];
			const used = new Set();

			tasks.forEach((task, index) => {
				if (used.has(index)) return;

				const members = [{ task, index }];
				used.add(index);

				if ((task.type || "action") !== "validation") {
					const validationIndex = tasks.findIndex((candidate, candidateIndex) => {
						return !used.has(candidateIndex)
							&& candidate.type === "validation"
							&& candidate.validates === task.id;
					});

					if (validationIndex !== -1) {
						members.push({ task: tasks[validationIndex], index: validationIndex });
						used.add(validationIndex);
					}
				}

				groups.push(members);
			});

			return groups;
		}

		function groupStatus(group, activeIndex, statuses) {
			const values = group.map(({ index }) => statuses[index] || (index < activeIndex ? "done" : index === activeIndex ? "running" : "pending"));
			if (values.includes("error")) return "error";
			if (values.includes("running")) return "running";
			if (values.every((value) => value === "done")) return "done";
			return "pending";
		}

		function renderTasks(state, activeIndex = -1, statuses = {}) {
			state.tasksView.innerHTML = "";

			buildTaskGroups(state.tasks).forEach((group, groupIndex) => {
				const primary = group[0].task;
				const status = groupStatus(group, activeIndex, statuses);
				const expanded = state.expandedTaskGroups.has(groupIndex);
				const card = document.createElement("article");
				card.className = `task-card ${status} ${expanded ? "expanded" : "collapsed"}`;
				card.dataset.groupIndex = groupIndex;

				const head = document.createElement("div");
				head.className = "task-head";

				const stateEl = document.createElement("div");
				stateEl.className = "task-state";
				stateEl.textContent = status === "done" ? "OK" : status === "running" ? "..." : status === "error" ? "!" : groupIndex + 1;

				const copy = document.createElement("div");
				const name = document.createElement("div");
				name.className = "task-name";
				name.textContent = group.length > 1
					? `${primary.id || `task_${groupIndex + 1}`} + ${group[1].task.id || "validation"}`
					: primary.id || `task_${groupIndex + 1}`;
				const description = document.createElement("div");
				description.className = "task-copy";
				description.textContent = primary.description || "";
				copy.append(name, description);

				const type = document.createElement("div");
				type.className = "task-type";
				type.textContent = group.length > 1 ? "step" : primary.type || "action";

				const toggle = document.createElement("button");
				toggle.className = "disclosure-button";
				toggle.type = "button";
				toggle.title = expanded ? "Fold details" : "Unfold details";
				toggle.setAttribute("aria-label", toggle.title);
				toggle.textContent = expanded ? "-" : "+";
				toggle.addEventListener("click", () => {
					if (state.expandedTaskGroups.has(groupIndex)) {
						state.expandedTaskGroups.delete(groupIndex);
					} else {
						state.expandedTaskGroups.add(groupIndex);
					}
					renderTasks(state, state.activeTaskIndex, state.taskStatuses);
				});

				head.append(stateEl, copy, type, toggle);
				card.appendChild(head);

				if (expanded) {
					const details = document.createElement("div");
					details.className = "task-details";

					group.forEach(({ task, index }) => {
						const detail = document.createElement("div");
						detail.className = "task-detail";

						const detailTitle = document.createElement("div");
						detailTitle.className = "task-detail-title";

						const detailId = document.createElement("span");
						detailId.textContent = task.id || `task_${index + 1}`;
						const detailType = document.createElement("span");
						detailType.className = "task-type";
						detailType.textContent = task.type || "action";
						detailTitle.append(detailId, detailType);

						const detailDescription = document.createElement("div");
						detailDescription.className = "task-detail-description";
						detailDescription.textContent = task.description || "";

						detail.append(detailTitle, detailDescription);

						if (state.taskOutputs[index]) {
							const output = document.createElement("div");
							output.className = "task-output markdown";
							output.innerHTML = renderMarkdown(state.taskOutputs[index]);
							enrichLocalMedia(output);
							detail.appendChild(output);
						}

						details.appendChild(detail);
					});

					card.appendChild(details);
				}

				state.tasksView.appendChild(card);
			});
		}

		function attachTaskOutput(state, index, text) {
			state.taskOutputs[index] = text || "(No response text returned.)";
		}

		function addSummary(state, text) {
			const summary = document.createElement("div");
			summary.className = "summary-card markdown";
			summary.innerHTML = renderMarkdown(text || "(No summary returned.)");
			enrichLocalMedia(summary);
			state.messagesView.appendChild(summary);
			state.body.scrollTop = state.body.scrollHeight;
		}

		async function postJson(url, payload) {
			const response = await fetch(url, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload),
			});

			const data = await response.json().catch(() => ({}));
			if (!response.ok) {
				throw new Error(data.error || `Request failed with status ${response.status}`);
			}

			return data;
		}

		function createWindow(prompt) {
			windowCount += 1;
			const id = `task-window-${Date.now()}-${windowCount}`;
			const offset = ((windowCount - 1) % 6) * 34;
			const left = Math.max(16, Math.min(window.innerWidth - 720, 80 + offset));
			const top = Math.max(72, Math.min(window.innerHeight - 720, 88 + offset));

			const el = document.createElement("section");
			el.className = "window";
			el.style.left = `${left}px`;
			el.style.top = `${top}px`;
			el.innerHTML = `
				<div class="titlebar">
					<div class="title-meta">
						<div class="window-title"></div>
						<div class="window-subtitle"></div>
					</div>
					<div class="window-actions">
						<button class="icon-button minimize" type="button" title="Minimize" aria-label="Minimize">-</button>
						<button class="icon-button close" type="button" title="Close" aria-label="Close">x</button>
					</div>
				</div>
				<div class="window-body">
					<div class="message-stack"></div>
					<div class="task-stack"></div>
				</div>
				<form class="composer">
					<textarea class="textbox reply-input" rows="1" placeholder="Continue this task..."></textarea>
					<button class="send-button reply-button" type="submit" title="Send" aria-label="Send">GO</button>
				</form>
			`;

			desktop.appendChild(el);

			const state = {
				id,
				el,
				prompt,
				messages: [],
				tasks: [],
				taskOutputs: {},
				taskStatuses: {},
				expandedTaskGroups: new Set(),
				activeTaskIndex: -1,
				status: "Created",
				title: el.querySelector(".window-title"),
				subtitle: el.querySelector(".window-subtitle"),
				body: el.querySelector(".window-body"),
				messagesView: el.querySelector(".message-stack"),
				tasksView: el.querySelector(".task-stack"),
				replyForm: el.querySelector(".composer"),
				replyInput: el.querySelector(".reply-input"),
				replyButton: el.querySelector(".reply-button"),
			};

			state.title.textContent = titleFromPrompt(prompt);
			setWindowStatus(state, "Queued");
			appendBubble(state, "user", prompt);

			el.addEventListener("pointerdown", () => focusWindow(state));
			el.querySelector(".close").addEventListener("click", () => closeWindow(state));
			el.querySelector(".minimize").addEventListener("click", () => minimizeWindow(state));
			state.replyForm.addEventListener("submit", (event) => {
				event.preventDefault();
				continueWindow(state);
			});

			makeDraggable(state);
			windows.set(id, state);
			focusWindow(state);
			updateEmptyState();
			return state;
		}

		function makeDraggable(state) {
			const titlebar = state.el.querySelector(".titlebar");
			let startX = 0;
			let startY = 0;
			let startLeft = 0;
			let startTop = 0;
			let dragging = false;

			titlebar.addEventListener("pointerdown", (event) => {
				if (event.target.closest("button") || window.matchMedia("(max-width: 780px)").matches) return;
				dragging = true;
				focusWindow(state);
				startX = event.clientX;
				startY = event.clientY;
				startLeft = Number.parseFloat(state.el.style.left) || 0;
				startTop = Number.parseFloat(state.el.style.top) || 0;
				titlebar.setPointerCapture(event.pointerId);
			});

			titlebar.addEventListener("pointermove", (event) => {
				if (!dragging) return;
				const nextLeft = Math.max(8, Math.min(window.innerWidth - 96, startLeft + event.clientX - startX));
				const nextTop = Math.max(58, Math.min(window.innerHeight - 118, startTop + event.clientY - startY));
				state.el.style.left = `${nextLeft}px`;
				state.el.style.top = `${nextTop}px`;
			});

			titlebar.addEventListener("pointerup", (event) => {
				dragging = false;
				if (titlebar.hasPointerCapture(event.pointerId)) {
					titlebar.releasePointerCapture(event.pointerId);
				}
			});
		}

		function closeWindow(state) {
			windows.delete(state.id);
			state.el.remove();
			const dockItem = dock.querySelector(`[data-window-id="${state.id}"]`);
			if (dockItem) dockItem.remove();
			updateEmptyState();
			setGlobalStatus(windows.size ? "Idle" : "Idle");
		}

		function minimizeWindow(state) {
			state.el.classList.add("minimized");
			if (dock.querySelector(`[data-window-id="${state.id}"]`)) return;

			const item = document.createElement("button");
			item.className = "dock-item";
			item.type = "button";
			item.dataset.windowId = state.id;
			item.textContent = state.title.textContent;
			item.title = `Restore ${state.title.textContent}`;
			item.addEventListener("click", () => {
				state.el.classList.remove("minimized");
				item.remove();
				focusWindow(state);
			});
			dock.appendChild(item);
		}

		async function runInitialTask(state) {
			state.replyInput.disabled = true;
			state.replyButton.disabled = true;

			try {
				setGlobalStatus("Planning");
				setWindowStatus(state, "Planning");
				const plan = await postJson("/plan", { prompt: state.prompt });
				state.tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
				state.messages = Array.isArray(plan.messages) ? plan.messages : [];
				renderTasks(state);

				if (!state.tasks.length) {
					setWindowStatus(state, "No tasks");
					addSummary(state, "No tasks were generated for this prompt.");
					return;
				}

				const statuses = {};
				state.taskStatuses = statuses;
				for (let index = 0; index < state.tasks.length; index += 1) {
					const task = state.tasks[index];
					setGlobalStatus(`Task ${index + 1}/${state.tasks.length}`);
					setWindowStatus(state, `Running ${index + 1}/${state.tasks.length}`);
					state.activeTaskIndex = index;
					renderTasks(state, index, statuses);

					const result = await postJson("/run_task", {
						task: task.description || "",
						messages: state.messages,
					});

					state.messages = Array.isArray(result.messages) ? result.messages : state.messages;
					statuses[index] = "done";
					attachTaskOutput(state, index, result.response);
					state.activeTaskIndex = index + 1;
					renderTasks(state, index + 1, statuses);
					setWindowStatus(state, `Completed ${index + 1}/${state.tasks.length}`);
				}

				setGlobalStatus("Summarizing");
				setWindowStatus(state, "Summarizing");
				const summary = await postJson("/summarize", { messages: state.messages });
				state.messages = Array.isArray(summary.messages) ? summary.messages : state.messages;
				addSummary(state, summary.summary);
				setWindowStatus(state, "Complete");
				setGlobalStatus("Idle");
			} catch (error) {
				setWindowStatus(state, "Error");
				setGlobalStatus("Error");
				showError(error.message || "Something went wrong.");
			} finally {
				state.replyInput.disabled = false;
				state.replyButton.disabled = false;
			}
		}

		async function continueWindow(state) {
			const prompt = state.replyInput.value.trim();
			if (!prompt) return;

			appendBubble(state, "user", prompt);
			state.replyInput.value = "";
			state.replyInput.disabled = true;
			state.replyButton.disabled = true;
			setGlobalStatus("Running");
			setWindowStatus(state, "Continuing");

			try {
				const result = await postJson("/run_task", {
					task: prompt,
					messages: state.messages,
				});
				state.messages = Array.isArray(result.messages) ? result.messages : state.messages;
				appendBubble(state, "agent", result.response);
				setWindowStatus(state, "Complete");
				setGlobalStatus("Idle");
			} catch (error) {
				setWindowStatus(state, "Error");
				setGlobalStatus("Error");
				showError(error.message || "Something went wrong.");
			} finally {
				state.replyInput.disabled = false;
				state.replyButton.disabled = false;
				state.replyInput.focus();
			}
		}

		launcherForm.addEventListener("submit", (event) => {
			event.preventDefault();
			const prompt = launcherInput.value.trim();
			if (!prompt) {
				showError("Enter a task before starting a new window.");
				return;
			}

			launcherInput.value = "";
			const state = createWindow(prompt);
			runInitialTask(state);
		});

		schedulerToggle.addEventListener("click", () => {
			const nextOpen = schedulerPanel.hidden;
			schedulerPanel.hidden = !nextOpen;
			schedulerToggle.setAttribute("aria-expanded", String(nextOpen));
			if (nextOpen) {
				refreshSchedulerStatus();
			}
		});

		[launcherInput].forEach((input) => {
			input.addEventListener("keydown", (event) => {
				if (event.key === "Enter" && !event.shiftKey) {
					event.preventDefault();
					launcherForm.requestSubmit();
				}
			});
		});

		window.addEventListener("resize", () => {
			resizeNodeField();
			windows.forEach((state) => {
				if (window.matchMedia("(max-width: 780px)").matches) return;
				const left = Number.parseFloat(state.el.style.left) || 0;
				const top = Number.parseFloat(state.el.style.top) || 0;
				state.el.style.left = `${Math.min(left, window.innerWidth - 110)}px`;
				state.el.style.top = `${Math.min(top, window.innerHeight - 130)}px`;
			});
		});

		window.addEventListener("pointermove", (event) => {
			if (event.pointerType === "touch") return;
			updatePointerTarget(event.clientX, event.clientY);
		});

		window.addEventListener("pointerleave", () => {
			pointerTargetX = 0;
			pointerTargetY = 0;
		});

		resizeNodeField();
		animateBackgroundDrift();
		nodePulseTimer = window.setInterval(pulseRandomNode, NODE_PULSE_INTERVAL_MS);
		checkHeartbeat();
		window.setInterval(checkHeartbeat, HEARTBEAT_INTERVAL_MS);
		refreshSchedulerStatus();
		window.setInterval(refreshSchedulerStatus, SCHEDULER_INTERVAL_MS);
