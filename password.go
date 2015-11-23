package main

import (
	"log"

	"github.com/tmc/keyring"
)

func Password(data Info) (pass string) {
	if data.Auth == "open" && data.LinkAuth == "none" {
	} else {
		password, err := keyring.Get("AirPort", data.Ssid)
		if err == nil {
			pass = password
		} else {
			log.Fatal(err)
		}
	}
	return
}
