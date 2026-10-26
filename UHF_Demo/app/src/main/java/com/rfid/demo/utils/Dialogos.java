package com.rfid.demo.utils;

import android.content.Context;
import android.view.View;
import android.widget.FrameLayout;

import com.rfid.demo.R;

/** Apoio aos AlertDialog que pedem um campo de texto. */
public class Dialogos {

    /**
     * O setView do AlertDialog cola o campo nas bordas do diálogo; esta moldura dá o
     * mesmo recuo lateral do título e da mensagem.
     */
    public static View comMargem(Context context, View campo) {
        FrameLayout moldura = new FrameLayout(context);
        int lado = context.getResources().getDimensionPixelSize(R.dimen.espaco_24);
        int topo = context.getResources().getDimensionPixelSize(R.dimen.espaco_8);
        moldura.setPadding(lado, topo, lado, 0);
        moldura.addView(campo);
        return moldura;
    }
}
