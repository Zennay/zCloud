// Floor plan constants shared by the scene, the walking logic and the tests (no three.js here).
export const MAX_SLOTS = 8;
export const DESK_W = 3.2, DESK_D = 1.4, SEAT_OFFSET = -1.0;
/** Desk centre for a work-floor slot: four desks per row, two rows. Mirrors office_staff.desk_for_slot on the server. */
export const deskForSlot = (slot) => ({x: [-13.5, -4.5, 4.5, 13.5][slot % 4], z: [-8.0, -12.4][Math.min(1, Math.floor(slot / 4))]});
export const seatForSlot = (slot) => { const d = deskForSlot(slot); return {x: d.x, z: d.z + SEAT_OFFSET}; };
export const BOARD_SPOT = {x: 7.6, z: 3.3};
