// Seat click logic from the supplied body; the sample ticket quantity is two.
var SelectSeats = [];
$(function () {
    $("td[data-status='5']").each(function () {
        SelectSeats.push($(this).attr("id"));
    });
    if (SelectSeats.length == parseInt("2")) {
        $('#btnCheckOut').removeClass("disabled");
    }
});
$("td[data-type='Empty']").click(function () {
    if (SelectSeats.indexOf($(this).attr("id")) > 0) {
        return false;
    }
    if (SelectSeats.length == parseInt("2")) {
        $("#" + SelectSeats[0]).find("img").attr("src", "../img/standard_available.png");
        SelectSeats.splice(0, 1);
    }
    SelectSeats.push($(this).attr("id"));
    $(this).find("img").attr("src", "../img/standard_selected.png");
    if (SelectSeats.length == parseInt("2")) {
        $('#btnCheckOut').removeClass("disabled");
    }
});
