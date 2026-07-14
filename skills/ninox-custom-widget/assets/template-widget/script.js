// Minimal widget logic. `Ninox` is the global injected by the SDK.
//
// onPropertiesChanged fires immediately once the host delivers values, and again
// whenever the builder changes a property. Register handlers, then call ready().

Ninox.onPropertiesChanged(function (props) {
  document.getElementById('title').textContent = props.title || 'My Widget';
});

// Signal the host that we're loaded and listening. Required — without it the
// widget never receives its initial property values.
Ninox.ready();
