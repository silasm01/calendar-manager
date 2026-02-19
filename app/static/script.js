let calendar;
let currentEvent = null;
let events = [];
let detailPanelOnLeft = false;
let bufferEventIds = [];

// In-memory cache for database data
let buffers = {};  // { uid: { before, after } }
let privacySettings = {};  // { uid: { useGenericTitle, useGenericDescription } }
let ignoredEvents = new Set();  // Set of UIDs

document.addEventListener('DOMContentLoaded', function () {
    initCalendar();
    loadInitialData();
});

async function loadInitialData() {
    // Load init data first (very fast - database only, ~0.01s)
    try {
        const initRes = await fetch('/api/init');
        const initData = await initRes.json();
        
        buffers = initData.buffers || {};
        privacySettings = initData.privacy || {};
        const ignoredArray = initData.ignored || [];
        ignoredEvents = new Set(ignoredArray);
    } catch (error) {
        console.error('Error loading init data:', error);
    }
    
    // Then load pending events (with correct state already populated)
    try {
        const eventsRes = await fetch('/api/pending_events');
        const eventsData = await eventsRes.json();
        loadPendingEvents(eventsData);
    } catch (error) {
        console.error('Error loading events:', error);
        showNotification('Failed to load events', 'error');
    }
}

async function loadDatabaseData() {
    // This is deprecated - use loadInitialData instead
    // Kept for backward compatibility if needed
    try {
        const [buffersRes, privacyRes, ignoredRes] = await Promise.all([
            fetch('/api/buffers'),
            fetch('/api/privacy'),
            fetch('/api/ignored')
        ]);
        
        buffers = await buffersRes.json();
        privacySettings = await privacyRes.json();
        const ignoredArray = await ignoredRes.json();
        ignoredEvents = new Set(ignoredArray);
        
        loadPendingEvents();
    } catch (error) {
        console.error('Error loading database data:', error);
        loadPendingEvents();
    }
}

function initCalendar() {
    const calendarEl = document.getElementById('calendar');
    calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: 'timeGridWeek',
        firstDay: 1,
        locale: 'en-gb',
        headerToolbar: {
            left: 'prev,next today',
            // center: 'title',
            // right: 'timeGridWeek,timeGridDay'
            right: ''
        },
        events: [],
        eventClick: function (info) {
            // If this is a buffer event, find and show the parent event instead
            if (info.event.extendedProps.isBuffer) {
                // Extract original event ID from buffer ID (format: "buffer-before-uid" or "buffer-after-uid")
                const parts = info.event.id.split('-');
                const originalUid = parts.slice(2).join('-'); // Handle UIDs that may contain hyphens
                const originalEvent = calendar.getEventById(originalUid);
                if (originalEvent) {
                    showEventDetail(originalEvent);
                }
                return;
            }
            showEventDetail(info.event);
        },
        height: 'auto',
        slotDuration: '01:00:00',
        slotLabelInterval: '01:00:00',
        eventTimeFormat: {
            hour: '2-digit',
            minute: '2-digit',
            meridiem: false,
            hour12: false
        }
    });
    calendar.render();
}

async function loadPendingEvents(eventsData = null) {
    try {
        // If eventsData is not provided, fetch it
        if (!eventsData) {
            const response = await fetch('/api/pending_events');
            eventsData = await response.json();
        }

        // console.log('API Response:', eventsData);
        // console.log('Response length:', eventsData.length);

        events = eventsData.map(e => {
            // Check if event is ignored (from in-memory cache)
            const isIgnored = ignoredEvents.has(e.uid);
            // Set colors based on status
            const isPending = e.status === 'pending' && !isIgnored;
            const isTimeChanged = e.status === 'time_changed';
            const isApproved = e.status === 'approved' && !isIgnored;
            
            let backgroundColor, borderColor, textColor;
            if (isPending) {
                backgroundColor = '#3b82f6';  // Blue for pending
                borderColor = '#3b82f6';
                textColor = 'white';
            } else if (isTimeChanged) {
                backgroundColor = '#fbbf24';  // Yellow for time changed
                borderColor = '#f59e0b';
                textColor = '#78350f';
            } else if (isIgnored) {
                backgroundColor = '#f87171';  // Red for ignored
                borderColor = '#dc2626';
                textColor = 'white';
            } else if (isApproved) {
                backgroundColor = '#86efac';  // Green for approved
                borderColor = '#22c55e';
                textColor = '#166534';
            } else {
                backgroundColor = '#cbd5e0';  // Grey fallback
                borderColor = '#cbd5e0';
                textColor = '#94a3b8';
            }
            
            const eventObj = {
                id: e.uid,
                title: e.title,
                start: e.start,
                end: e.end,
                backgroundColor: backgroundColor,
                borderColor: borderColor,
                textColor: textColor,
                extendedProps: { ...e, isIgnored: isIgnored }
            };
            // console.log('Mapped event:', eventObj);
            return eventObj;
        });

        // console.log('Total events to add:', events.length);

        // Render events to the calendar
        calendar.removeAllEvents();
        events.forEach(event => {
            calendar.addEvent(event);
        });

        // Add buffer visualizations for all events with saved buffer values
        addAllBufferVisualizations();

    } catch (error) {
        console.error('Error loading events:', error);
        showNotification('Failed to load events', 'error');
    }
}

function showEventDetail(event) {
    currentEvent = event;
    const props = event.extendedProps;

    document.getElementById('eventTitle').textContent = event.title;
    document.getElementById('eventSource').textContent = props.source || 'Unknown';

    // Format date and time nicely
    const startDate = new Date(event.start);
    const endDate = new Date(event.end);
    const dateOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    const timeOptions = { hour: '2-digit', minute: '2-digit', hour12: false };

    const formattedDate = startDate.toLocaleDateString('en-GB', dateOptions);
    const formattedStartTime = startDate.toLocaleTimeString('en-GB', timeOptions);
    const formattedEndTime = endDate.toLocaleTimeString('en-GB', timeOptions);

    document.getElementById('eventTime').innerHTML = `<strong>${formattedDate}</strong><br>${formattedStartTime} – ${formattedEndTime}`;

    document.getElementById('eventDescription').textContent = props.description || 'No description';

    // Load saved buffer values from localStorage or use defaults
    const savedBuffers = getBufferValues(event.id);
    document.getElementById('bufferBefore').value = savedBuffers.before;
    document.getElementById('bufferAfter').value = savedBuffers.after;
    document.getElementById('eventUid').textContent = event.id;

    // Get event state flags
    const isWork = props.source === 'Work';
    const isApproved = props.status === 'approved';
    const isIgnored = props.isIgnored || false;
    const isTimeChanged = props.status === 'time_changed';
    const isPending = props.status === 'pending';

    const statusMap = { 'approved': 'Approved', 'time_changed': 'Time Changed ⚠️', 'ignored': 'Ignored', 'ignored_auto': 'Auto-Ignored' };
    let statusDisplay;
    
    if (isIgnored) {
        statusDisplay = 'Ignored';
    } else {
        statusDisplay = statusMap[props.status] || (statusMap[props.decision] || 'Pending');
    }
    
    document.getElementById('eventStatus').textContent = statusDisplay;
    document.getElementById('eventStatus').className = 'status-badge status-' + (isIgnored ? 'ignored' : (props.status || props.decision || 'pending'));

    if (props.conflicts && props.conflicts.length > 0) {
        document.getElementById('eventConflicts').style.display = 'block';
        document.getElementById('conflictList').textContent = props.conflicts.join(', ');
    } else {
        document.getElementById('eventConflicts').style.display = 'none';
    }

    // Handle action buttons - allow changing mind except for Work events
    // Work events: hide all buttons
    // Otherwise: show buttons with individual disabled states
    const approveBtn = document.getElementById('approveBtn');
    const ignoreBtn = document.getElementById('ignoreBtn');
    
    if (isWork) {
        document.getElementById('eventActions').style.display = 'none';
    } else {
        document.getElementById('eventActions').style.display = 'flex';
        
        // Set button states based on current status
        // Check isIgnored FIRST before checking status
        if (isIgnored) {
            // Ignored: can change mind to approve, but not ignore again
            approveBtn.disabled = false;
            ignoreBtn.disabled = true;
        } else if (isTimeChanged || isPending) {
            // Both buttons enabled
            approveBtn.disabled = false;
            ignoreBtn.disabled = false;
        } else if (isApproved) {
            // Approved (and not ignored): can change mind to ignore, but not approve again
            approveBtn.disabled = true;
            ignoreBtn.disabled = false;
        } else {
            // Default: both enabled
            approveBtn.disabled = false;
            ignoreBtn.disabled = false;
        }
    }
    
    // Disable buffer inputs and privacy settings only for Work events
    const buffersDisabled = isWork;
    document.getElementById('bufferBefore').disabled = buffersDisabled;
    document.getElementById('bufferAfter').disabled = buffersDisabled;

    // Load and set privacy preferences
    const privacySettings = getPrivacySettings(event.id);
    document.getElementById('useGenericTitle').checked = privacySettings.useGenericTitle;
    document.getElementById('useGenericDescription').checked = privacySettings.useGenericDescription;
    document.getElementById('useGenericTitle').disabled = buffersDisabled;
    document.getElementById('useGenericDescription').disabled = buffersDisabled;

    // Add event listeners for privacy settings
    document.getElementById('useGenericTitle').removeEventListener('change', onPrivacyChange);
    document.getElementById('useGenericDescription').removeEventListener('change', onPrivacyChange);
    document.getElementById('useGenericTitle').addEventListener('change', onPrivacyChange);
    document.getElementById('useGenericDescription').addEventListener('change', onPrivacyChange);

    // Remove old event listeners and add new ones
    const bufferBeforeInput = document.getElementById('bufferBefore');
    const bufferAfterInput = document.getElementById('bufferAfter');

    bufferBeforeInput.removeEventListener('input', onBufferInput);
    bufferAfterInput.removeEventListener('input', onBufferInput);
    bufferBeforeInput.addEventListener('input', onBufferInput);
    bufferAfterInput.addEventListener('input', onBufferInput);

    // Initial visualization
    updateBufferVisualization();
}

function onBufferInput() {
    if (!currentEvent) return;
    saveBufferValues(currentEvent.id,
        parseInt(document.getElementById('bufferBefore').value) || 0,
        parseInt(document.getElementById('bufferAfter').value) || 0
    );
    updateBufferVisualization();
}

function getBufferValues(uid) {
    return buffers[uid] || { before: 0, after: 0 };
}

function saveBufferValues(uid, before, after) {
    // Update in-memory cache
    buffers[uid] = { before, after };
    
    // Save to database
    if (currentEvent) {
        fetch('/api/buffers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                uid: uid,
                source: currentEvent.extendedProps.source,
                buffer_before: before,
                buffer_after: after
            })
        }).catch(error => console.error('Error saving buffers:', error));
    }
}

function getPrivacySettings(uid) {
    return privacySettings[uid] || { useGenericTitle: false, useGenericDescription: false };
}

function savePrivacySettings(uid, useGenericTitle, useGenericDescription) {
    // Update in-memory cache
    privacySettings[uid] = { useGenericTitle, useGenericDescription };
    
    // Save to database
    if (currentEvent) {
        fetch('/api/privacy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                uid: uid,
                source: currentEvent.extendedProps.source,
                use_generic_title: useGenericTitle,
                use_generic_description: useGenericDescription
            })
        }).catch(error => console.error('Error saving privacy settings:', error));
    }
}

function onPrivacyChange() {
    if (!currentEvent) return;
    savePrivacySettings(currentEvent.id,
        document.getElementById('useGenericTitle').checked,
        document.getElementById('useGenericDescription').checked
    );
}

function addAllBufferVisualizations() {
    events.forEach(event => {
        const buffer = buffers[event.id];
        if (buffer && (buffer.before > 0 || buffer.after > 0)) {
            addBufferVisualization(event, buffer.before, buffer.after);
        }
    });
}

function addBufferVisualization(event, bufferBefore, bufferAfter) {
    const startDate = new Date(event.start);
    const endDate = new Date(event.end);
    
    // Determine buffer color based on event status and ignored state
    const isApproved = event.extendedProps.status === 'approved';
    const isIgnored = event.extendedProps.isIgnored || false;
    const isTimeChanged = event.extendedProps.status === 'time_changed';
    const isPending = event.extendedProps.status === 'pending' && !isIgnored;
    
    let bufferColor;
    if (isIgnored) {
        bufferColor = '#f87171';  // Red for ignored
    } else if (isApproved) {
        bufferColor = '#4ade80';  // Darker green for approved
    } else if (isTimeChanged || isPending) {
        bufferColor = '#fbbf24';  // Yellow for pending/time_changed
    } else {
        bufferColor = '#cbd5e0';  // Grey fallback
    }

    // Buffer before event
    if (bufferBefore > 0) {
        const bufferBeforeStart = new Date(startDate.getTime() - bufferBefore * 60000);
        const bufferId = `buffer-before-${event.id}`;

        // Only add if not already present
        if (!calendar.getEventById(bufferId)) {
            const bufferEvent = {
                id: bufferId,
                title: `Buffer (${bufferBefore}m)`,
                start: bufferBeforeStart,
                end: startDate,
                backgroundColor: bufferColor,
                borderColor: bufferColor,
                display: 'block',
                extendedProps: { isBuffer: true }
            };
            calendar.addEvent(bufferEvent);
        }
    }

    // Buffer after event
    if (bufferAfter > 0) {
        const bufferAfterEnd = new Date(endDate.getTime() + bufferAfter * 60000);
        const bufferId = `buffer-after-${event.id}`;

        // Only add if not already present
        if (!calendar.getEventById(bufferId)) {
            const bufferEvent = {
                id: bufferId,
                title: `Buffer (${bufferAfter}m)`,
                start: endDate,
                end: bufferAfterEnd,
                backgroundColor: bufferColor,
                borderColor: bufferColor,
                display: 'block',
                extendedProps: { isBuffer: true }
            };
            calendar.addEvent(bufferEvent);
        }
    }
}

function updateBufferVisualization() {
    if (!currentEvent) return;

    // Remove old buffer events for this event
    const bufferBeforeId = `buffer-before-${currentEvent.id}`;
    const bufferAfterId = `buffer-after-${currentEvent.id}`;

    const oldBefore = calendar.getEventById(bufferBeforeId);
    if (oldBefore) oldBefore.remove();

    const oldAfter = calendar.getEventById(bufferAfterId);
    if (oldAfter) oldAfter.remove();

    const bufferBefore = parseInt(document.getElementById('bufferBefore').value) || 0;
    const bufferAfter = parseInt(document.getElementById('bufferAfter').value) || 0;

    if (bufferBefore === 0 && bufferAfter === 0) return;

    // Use the helper function to add visualization
    addBufferVisualization(currentEvent, bufferBefore, bufferAfter);
}

function closeEventDetail() {
    // Remove buffer visualization events
    bufferEventIds.forEach(id => {
        const event = calendar.getEventById(id);
        if (event) event.remove();
    });
    bufferEventIds = [];

    currentEvent = null;
    document.getElementById('eventTitle').textContent = 'No event selected';
    document.getElementById('eventSource').textContent = '';
    document.getElementById('eventTime').textContent = '';
    document.getElementById('eventDescription').textContent = '';
    document.getElementById('eventStatus').textContent = '';
    document.getElementById('eventUid').textContent = '';
    document.getElementById('eventConflicts').style.display = 'none';
}

async function approveEvent() {
    if (!currentEvent) return;

    try {
        const props = currentEvent.extendedProps;
        const wasIgnored = ignoredEvents.has(currentEvent.id);
        
        const response = await fetch('/api/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                uid: currentEvent.id,
                source: props.source,
                start: currentEvent.start,
                end: currentEvent.end,
                title: props.title,
                description: props.description,
                buffer_before: parseInt(document.getElementById('bufferBefore').value) || 0,
                buffer_after: parseInt(document.getElementById('bufferAfter').value) || 0,
                use_generic_title: document.getElementById('useGenericTitle').checked,
                use_generic_description: document.getElementById('useGenericDescription').checked
            })
        });

        if (response.ok) {
            // If this was a previously ignored event, remove it from the ignored list
            if (wasIgnored) {
                ignoredEvents.delete(currentEvent.id);
                // Wait for API to remove from database
                await fetch(`/api/ignored/${currentEvent.id}`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' }
                });
            }
            showNotification('Event approved!', 'success');
            closeEventDetail();
            // Reload events after all operations complete
            await loadPendingEvents();
        } else {
            showNotification('Failed to approve event', 'error');
        }
    } catch (error) {
        console.error('Error approving event:', error);
        showNotification('Error approving event', 'error');
    }
}




async function ignoreEvent() {
    if (!currentEvent) return;

    try {
        const props = currentEvent.extendedProps;
        const isTimeChanged = props.status === 'time_changed';
        const isApproved = props.status === 'approved';
        
        // If time_changed or approved, remove from blocked calendars first
        if (isTimeChanged || isApproved) {
            const removeResponse = await fetch('/api/remove-approval', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: currentEvent.id })
            });
            
            if (!removeResponse.ok) {
                console.warn('Failed to remove approval from blocked calendars');
            }
        }
        
        // Add to in-memory cache
        ignoredEvents.add(currentEvent.id);
        
        // Save to database
        const response = await fetch('/api/ignored', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid: currentEvent.id })
        });

        if (response.ok) {
            showNotification('Event ignored', 'success');
            closeEventDetail();
            loadPendingEvents();
        } else {
            showNotification('Failed to ignore event', 'error');
        }
    } catch (error) {
        console.error('Error ignoring event:', error);
        showNotification('Error ignoring event', 'error');
    }
}

function showNotification(message, type = 'success') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    document.body.appendChild(notification);
    setTimeout(() => notification.remove(), 3000);
}

document.addEventListener('click', function (e) {
    if (e.target === document.getElementById('eventDetail')) {
        closeEventDetail();
    }
});
