package main

import (
	"log"

	"github.com/tmc/keyring"
)

func Password(data Info) (pass string) {
	if data.Auth == "open" && data.LinkAuth == "none" {
	} else {
		pass, err := keyring.Get("AirPort", data.Ssid)
		if err == nil {
			data.Password = pass
		} else {
			log.Fatal(err)
		}
	}
	return
}
