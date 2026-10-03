// Fill the details popup from the data-* attributes of the clicked leave.
document.getElementById('leaveModal').addEventListener('show.bs.modal', function (event) {
    var d = event.relatedTarget.dataset;
    document.getElementById('m-employee').textContent = d.employee;
    document.getElementById('m-type').textContent = d.type;
    document.getElementById('m-start').textContent = d.start;
    document.getElementById('m-end').textContent = d.end;
    document.getElementById('m-days').textContent = d.days;
    document.getElementById('m-status').textContent = d.status;
    document.getElementById('m-reason').textContent = d.reason;
});
