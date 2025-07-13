
const generate_bwm_list = () => {
	const bwm_list = [
		101,
		102,
		103,
		104,
		105,
		106,
		107,
		108,
		109,
		110,
		111,
		112,
		113,
		114,
		115,
		116,
		117,
	];

	let channel = 101;

for(const bwm of bwm_list){
console.log(`
; BWM ${bwm}  ->  NE${channel}

TRF NE${channel} = E${bwm}.1, E${bwm}.2, E${bwm}.3, E${bwm}.4

KOPIE A${bwm}.1 = NE${channel}.8

TRFAD NE${++channel} AE${bwm}.1 1
TRFAD NE${++channel} AE${bwm}.2 1
`);
channel++;
}
};

const generate_dim_list = () => {
	const dim_list = [
		41,
		42,
		43,
		44,
		45
	];
	const dim_names = [
		"Kitchen Balls Small",
		"Kitchen Balls Big",
		"Lounge Lamp",
		"Lounge Balls Big",
		"Lounge Balls Small",
		"Maxwell Lights",
		"Sauna Lights 1",
		"Sauna Lights 2",  
		"Sauna Lights 3",
		"Sauna Lights 4"
	];
	let channel = 30;
	let index = 0;

for(dim of dim_list){

const base_ch = channel;
const dim_ch = ++channel;
console.log(`
; ${dim_names[index++]}
; DIM ${index}

TRFDA AA${dim}.1 NE${dim_ch} 2s NE${base_ch}.1
TRFDA AA${dim}.1 %0 2s !NE${base_ch}.1
TRFB NE${dim_ch} %100 !NE${base_ch}.2


; ${dim_names[index++]}
; DIM ${index}

TRFDA AA${dim}.2 NE${dim_ch+2} 2s NE${base_ch+2}.1
TRFDA AA${dim}.2 %0 2s !NE${base_ch+2}.1
TRFB NE${dim_ch+2} %100 !NE${base_ch+2}.2`);
channel+=3;
}
};

const generate_button_grid_list = () => {


	const button_grid_list = [
		51,
		52,
		53
	];

	let address = 51;

for(const button_grid of button_grid_list){
console.log(`
; SW ${button_grid}  -> NE${address}

; BUTTONS

; turn bits off again after 1s, give modbus enough time to read them
AUTOOFF NE${address}.1 1s
AUTOOFF NE${address}.2 1s
AUTOOFF NE${address}.3 1s
AUTOOFF NE${address}.4 1s
AUTOOFF NE${address}.5 1s
AUTOOFF NE${address}.6 1s

; rising edge detection
HFLANKE M${button_grid}.1 E${button_grid}.1
HFLANKE M${button_grid}.2 E${button_grid}.2
HFLANKE M${button_grid}.3 E${button_grid}.3
HFLANKE M${button_grid}.4 E${button_grid}.4
HFLANKE M${button_grid}.5 E${button_grid}.5
HFLANKE M${button_grid}.6 E${button_grid}.6

; set bit on on press
SET NE${address}.1 M${button_grid}.1
SET NE${address}.2 M${button_grid}.2
SET NE${address}.3 M${button_grid}.3
SET NE${address}.4 M${button_grid}.4
SET NE${address}.5 M${button_grid}.5
SET NE${address}.6 M${button_grid}.6

; LEDs
KOPIE A${button_grid}.1 NE${++address}.1
KOPIE A${button_grid}.2 NE${address}.2
KOPIE A${button_grid}.3 NE${address}.3
KOPIE A${button_grid}.4 NE${address}.4
KOPIE A${button_grid}.5 NE${address}.5
KOPIE A${button_grid}.6 NE${address}.6
KOPIE A${button_grid}.7 NE${address}.7
`);
address++;
}
};




generate_bwm_list();
generate_dim_list();

generate_button_grid_list();
