(() => {
  const element = document.getElementById('schedule-calendar');
  if (!element) return;
  const status = document.getElementById('calendar-status');
  if (!window.FullCalendar || !window.luxon) {
    status.textContent = 'Nie udało się wczytać kalendarza. Odśwież stronę.';
    status.hidden = false;
    return;
  }
  const events = JSON.parse(document.getElementById('calendar-events').textContent);
  const mobile = window.matchMedia('(max-width: 680px)');
  const dialog = document.getElementById('lesson-details');
  const dateFormat = new Intl.DateTimeFormat('pl-PL', {
    timeZone: 'Europe/Warsaw', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
  });
  const timeFormat = new Intl.DateTimeFormat('pl-PL', {
    timeZone: 'Europe/Warsaw', hour: '2-digit', minute: '2-digit'
  });
  function showDetails(event) {
    document.getElementById('lesson-title').textContent = event.title;
    const kind = document.getElementById('lesson-kind');
    kind.textContent = event.extendedProps.online ? 'Zdalne' : 'Stacjonarne';
    kind.className = `lesson-key ${event.extendedProps.online ? 'online' : 'inperson'}`;
    document.getElementById('lesson-date').textContent = dateFormat.format(event.start);
    document.getElementById('lesson-time').textContent = `${timeFormat.format(event.start)}–${timeFormat.format(event.end)}`;
    const location = document.getElementById('lesson-location');
    location.textContent = event.extendedProps.location;
    location.hidden = !event.extendedProps.location;
    if (event.extendedProps.mapsUrl) {
      location.href = event.extendedProps.mapsUrl;
    } else {
      location.removeAttribute('href');
    }
    dialog.showModal();
  }
  document.getElementById('close-lesson').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  const calendar = new FullCalendar.Calendar(element, {
    locale: 'pl',
    timeZone: 'Europe/Warsaw',
    initialDate: element.dataset.initialDate,
    initialView: mobile.matches ? 'listWeek' : 'timeGridWeek',
    firstDay: 1,
    headerToolbar: {
      left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek'
    },
    buttonText: { today: 'Dzisiaj', month: 'Miesiąc', week: 'Tydzień', day: 'Dzień', list: 'Lista' },
    allDaySlot: false,
    nowIndicator: true,
    editable: false,
    eventStartEditable: false,
    eventDurationEditable: false,
    height: 'auto',
    slotMinTime: '06:00:00',
    slotMaxTime: '23:00:00',
    slotDuration: '00:30:00',
    scrollTime: '07:00:00',
    dayMaxEvents: 3,
    eventDisplay: 'block',
    displayEventEnd: true,
    eventTimeFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
    slotLabelFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
    noEventsContent: 'Brak zajęć w tym okresie.',
    events,
    eventClick: ({ event }) => showDetails(event),
    eventDidMount: ({ el, event }) => {
      el.setAttribute('tabindex', '0');
      el.setAttribute('role', 'button');
      el.setAttribute('aria-label', `${timeFormat.format(event.start)}–${timeFormat.format(event.end)}, ${event.title}, ${event.extendedProps.online ? 'zdalne' : 'stacjonarne'}`);
      el.title = `${event.title}\n${event.extendedProps.location || ''}`;
      el.addEventListener('keydown', (key) => {
        if (key.key === 'Enter' || key.key === ' ') {
          key.preventDefault();
          showDetails(event);
        }
      });
    },
    datesSet: ({ start, end }) => {
      const visible = events.filter(event => new Date(event.start) < end && new Date(event.end) > start);
      status.hidden = visible.length > 0;
      status.textContent = 'Brak zajęć w tym okresie. Wybierz inny tydzień lub miesiąc.';
      // Keep unusual early/late classes visible, without adding empty night hours normally.
      const times = visible.flatMap(event => [event.start.slice(11, 13), event.end.slice(11, 13)]).map(Number);
      const firstHour = Math.min(6, ...times);
      const lastHour = Math.min(24, Math.max(23, ...times.map(hour => hour + 1)));
      calendar.setOption('slotMinTime', `${String(firstHour).padStart(2, '0')}:00:00`);
      calendar.setOption('slotMaxTime', `${lastHour}:00:00`);
    }
  });
  calendar.render();
  mobile.addEventListener('change', ({ matches }) => calendar.changeView(matches ? 'listWeek' : 'timeGridWeek'));
})();
